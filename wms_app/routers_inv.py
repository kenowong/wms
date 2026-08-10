# -*- coding: utf-8 -*-
"""API路由 - 库存、盘点、发票、统计"""
import os
from fastapi import APIRouter, HTTPException, Depends, File, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List
from database import get_conn
from period_guard import assert_dates_open
from routers_order import gen_no, update_inventory
from routers_auth import get_current_user
from ocr_utils import save_upload, recognize_invoice_file, get_data_dir
import datetime

router = APIRouter()

# ══════════════════════════════════════════
# 库存台账
# ══════════════════════════════════════════
@router.get("/inventory")
def list_inventory(keyword: str = "", warehouse_id: int = -1, project_id: int = -1, alert: int = 0):
    conn = get_conn()
    sql = """SELECT i.*,
        g.code as goods_code, g.name as goods_name, g.spec, g.model, g.unit,
        g.stock_min, g.stock_max,
        w.name as warehouse_name,
        COALESCE(p.name,'公共') as project_name
        FROM inventory i
        JOIN goods g ON i.goods_id=g.id
        JOIN warehouses w ON i.warehouse_id=w.id
        LEFT JOIN projects p ON i.project_id=p.id
        WHERE 1=1"""
    params = []
    if keyword:
        sql += " AND (g.name LIKE ? OR g.code LIKE ?)"
        params += [f"%{keyword}%",f"%{keyword}%"]
    if warehouse_id >= 0:
        sql += " AND i.warehouse_id=?"
        params.append(warehouse_id)
    if project_id >= 0:
        sql += " AND i.project_id=?"
        params.append(project_id)
    if alert == 1:
        sql += " AND i.qty <= g.stock_min AND g.stock_min > 0"
    sql += " ORDER BY g.name"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@router.get("/inventory/logs")
def list_logs(keyword: str = "", biz_type: str = "", project_id: int = -1,
              date_from: str = "", date_to: str = "", limit: int = 200):
    conn = get_conn()
    sql = "SELECT * FROM inventory_logs WHERE 1=1"
    params = []
    if keyword:
        sql += " AND (goods_name LIKE ? OR biz_order_no LIKE ?)"
        params += [f"%{keyword}%",f"%{keyword}%"]
    if biz_type:
        sql += " AND biz_type=?"
        params.append(biz_type)
    if project_id >= 0:
        sql += " AND project_id=?"
        params.append(project_id)
    if date_from:
        sql += " AND created_at >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND created_at <= ?"
        params.append(date_to + " 23:59:59")
    sql += f" ORDER BY id DESC LIMIT {limit}"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ══════════════════════════════════════════
# 库存盘点
# ══════════════════════════════════════════
@router.get("/check")
def list_checks(operator: str = "", business_person: str = ""):
    conn = get_conn()
    sql = """SELECT c.*, w.name as warehouse_name FROM inventory_checks c
        LEFT JOIN warehouses w ON c.warehouse_id=w.id WHERE 1=1"""
    params = []
    if operator:
        sql += " AND c.operator=?"
        params.append(operator)
    if business_person:
        sql += " AND c.business_person=?"
        params.append(business_person)
    sql += " ORDER BY c.id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@router.post("/check")
def create_check(data: dict, user: dict = Depends(get_current_user)):
    conn = get_conn()
    try:
        assert_dates_open(conn, data.get('check_date', ''), label="盘点单")
        check_no = gen_no("IC", conn)
        wid = data.get('warehouse_id')
        pid = data.get('project_id', 0)
        conn.execute("""INSERT INTO inventory_checks(check_no,warehouse_id,project_id,check_date,status,operator,business_person,remark)
            VALUES(?,?,?,?,1,?,?,?)""",
            (check_no, wid, pid, data.get('check_date',''), user['display_name'], data.get('business_person',''), data.get('remark','')))
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # 自动生成盘点明细（从库存台账）
        rows = conn.execute(
            "SELECT i.*,g.name,g.unit FROM inventory i JOIN goods g ON i.goods_id=g.id "
            "WHERE i.warehouse_id=? AND i.project_id=?", (wid, pid)).fetchall()
        for r in rows:
            conn.execute("""INSERT INTO check_items(check_id,goods_id,goods_name,unit,system_qty,actual_qty,diff_qty,cost_price)
                VALUES(?,?,?,?,?,?,0,?)""",
                (cid, r['goods_id'], r['name'], r['unit'], r['qty'], r['qty'], r['avg_cost']))
        conn.commit()
        return {"ok": True, "check_no": check_no, "id": cid}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()

@router.get("/check/{cid}")
def get_check(cid: int):
    conn = get_conn()
    row = conn.execute("""SELECT c.*, w.name as warehouse_name FROM inventory_checks c
        LEFT JOIN warehouses w ON c.warehouse_id=w.id WHERE c.id=?""", (cid,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(404)
    result = dict(row)
    items = conn.execute("SELECT * FROM check_items WHERE check_id=?", (cid,)).fetchall()
    result['items'] = [dict(i) for i in items]
    conn.close()
    return result

def _check_reverse_stock(conn, cid, row):
    """把 confirm 时调整过的库存差异反向调回（撤销盘点入账）"""
    items = conn.execute("SELECT * FROM check_items WHERE check_id=?", (cid,)).fetchall()
    for item in items:
        if item['diff_qty'] == 0:
            continue
        inv = conn.execute(
            "SELECT qty,avg_cost,total_cost FROM inventory WHERE goods_id=? AND warehouse_id=? AND project_id=?",
            (item['goods_id'], row['warehouse_id'], row['project_id'])).fetchone()
        if not inv:
            continue
        new_qty = inv['qty'] - item['diff_qty']
        new_total = new_qty * inv['avg_cost']
        conn.execute(
            "UPDATE inventory SET qty=?,total_cost=?,updated_at=datetime('now','localtime') "
            "WHERE goods_id=? AND warehouse_id=? AND project_id=?",
            (new_qty, new_total, item['goods_id'], row['warehouse_id'], row['project_id']))
        direction = -1 if item['diff_qty'] > 0 else 1
        qty = abs(item['diff_qty'])
        conn.execute("""INSERT INTO inventory_logs(goods_id,goods_name,warehouse_id,project_id,
            biz_type,biz_order_no,direction,qty,before_qty,after_qty,cost_price)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (item['goods_id'], item['goods_name'], row['warehouse_id'], row['project_id'],
             'check_reverse', row['check_no'], direction, qty, inv['qty'], new_qty, item['cost_price']))

@router.put("/check/{cid}/items")
def update_check_items(cid: int, items: List[dict]):
    conn = get_conn()
    row = conn.execute("SELECT status,check_date FROM inventory_checks WHERE id=?", (cid,)).fetchone()
    if row and row['status'] >= 2:
        conn.close(); raise HTTPException(400, "盘点单已确认/已作废，明细不能修改。请先反审核。")
    if row:
        assert_dates_open(conn, row['check_date'], label="盘点单")
    for item in items:
        diff = item.get('actual_qty', 0) - item.get('system_qty', 0)
        diff_amt = diff * item.get('cost_price', 0)
        conn.execute("""UPDATE check_items SET actual_qty=?,diff_qty=?,diff_amount=? WHERE id=?""",
            (item.get('actual_qty',0), diff, diff_amt, item['id']))
    conn.commit(); conn.close()
    return {"ok": True}

@router.post("/check/{cid}/unapprove")
def unapprove_check(cid: int):
    """反审核：回滚库存差异 + 状态回草稿"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM inventory_checks WHERE id=?", (cid,)).fetchone()
    if not row: conn.close(); raise HTTPException(404)
    assert_dates_open(conn, row['check_date'], label="盘点单")
    if row['status'] != 2: conn.close(); raise HTTPException(400, "仅已确认(已审核)的盘点单可反审核")
    _check_reverse_stock(conn, cid, row)
    conn.execute("UPDATE inventory_checks SET status=0 WHERE id=?", (cid,))
    conn.commit(); conn.close(); return {"ok": True}

@router.post("/check/{cid}/void")
def void_check(cid: int):
    """作废：若已确认则回滚库存，并标记作废"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM inventory_checks WHERE id=?", (cid,)).fetchone()
    if not row: conn.close(); raise HTTPException(404)
    assert_dates_open(conn, row['check_date'], label="盘点单")
    if row['status'] == 3: conn.close(); raise HTTPException(400, "已作废")
    if row['status'] == 2:
        _check_reverse_stock(conn, cid, row)
    conn.execute("UPDATE inventory_checks SET status=3 WHERE id=?", (cid,))
    conn.commit(); conn.close(); return {"ok": True}

@router.post("/check/{cid}/confirm")
def confirm_check(cid: int, body: dict = {}):
    """确认盘点结果并调整库存"""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM inventory_checks WHERE id=?", (cid,)).fetchone()
        if not row: raise HTTPException(404)
        if row['status'] == 2: raise HTTPException(400, "已完成")
        assert_dates_open(conn, row['check_date'], label="盘点单")
        items = conn.execute("SELECT * FROM check_items WHERE check_id=?", (cid,)).fetchall()
        for item in items:
            if item['diff_qty'] != 0:
                direction = 1 if item['diff_qty'] > 0 else -1
                qty = abs(item['diff_qty'])
                # 直接调整库存（不走update_inventory的库存不足检查）
                inv = conn.execute(
                    "SELECT qty,avg_cost,total_cost FROM inventory WHERE goods_id=? AND warehouse_id=? AND project_id=?",
                    (item['goods_id'], row['warehouse_id'], row['project_id'])).fetchone()
                if inv:
                    new_qty = inv['qty'] + item['diff_qty']
                    new_total = new_qty * inv['avg_cost']
                    conn.execute(
                        "UPDATE inventory SET qty=?,total_cost=?,updated_at=datetime('now','localtime') "
                        "WHERE goods_id=? AND warehouse_id=? AND project_id=?",
                        (new_qty, new_total, item['goods_id'], row['warehouse_id'], row['project_id']))
                    conn.execute("""INSERT INTO inventory_logs(goods_id,goods_name,warehouse_id,project_id,
                        biz_type,biz_order_no,direction,qty,before_qty,after_qty,cost_price)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (item['goods_id'], item['goods_name'], row['warehouse_id'], row['project_id'],
                         'check', row['check_no'], direction, qty, inv['qty'], new_qty, item['cost_price']))
        conn.execute("UPDATE inventory_checks SET status=2 WHERE id=?", (cid,))
        conn.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback(); raise HTTPException(400, str(e))
    finally:
        conn.close()

# ══════════════════════════════════════════
# 发票管理
# ══════════════════════════════════════════
class InvoiceModel(BaseModel):
    invoice_no: Optional[str] = ""
    invoice_code: Optional[str] = ""
    invoice_type: Optional[str] = "增值税普通发票"
    direction: Optional[int] = 1
    project_id: Optional[int] = 0
    partner_id: Optional[int] = None
    invoice_date: Optional[str] = ""
    tax_rate: Optional[float] = 13.0
    untax_amount: Optional[float] = 0
    tax_amount: Optional[float] = 0
    total_amount: Optional[float] = 0
    status: Optional[str] = "已开具"
    remark: Optional[str] = ""
    image_path: Optional[str] = ""      # 发票图片（相对数据目录）
    ocr_raw: Optional[str] = ""         # 识别原始串（二维码/ocr 文本），便于追溯
    relations: Optional[list] = []      # 关联单据（选单开具时传入）：[{order_type,order_id,order_no,amount}]

@router.get("/invoices")
def list_invoices(direction: int = -1, keyword: str = "", status: str = "",
                  project_id: int = -1, date_from: str = "", date_to: str = ""):
    conn = get_conn()
    sql = """SELECT i.*, COALESCE(p.name,'') as partner_name_ref,
        COALESCE(pj.name,'') as project_name
        FROM invoices i
        LEFT JOIN partners p ON i.partner_id=p.id
        LEFT JOIN projects pj ON i.project_id=pj.id
        WHERE 1=1"""
    params = []
    if direction >= 0:
        sql += " AND i.direction=?"
        params.append(direction)
    if keyword:
        sql += " AND (i.invoice_no LIKE ? OR i.partner_name LIKE ?)"
        params += [f"%{keyword}%",f"%{keyword}%"]
    if status:
        sql += " AND i.status=?"
        params.append(status)
    if project_id >= 0:
        sql += " AND i.project_id=?"
        params.append(project_id)
    if date_from:
        sql += " AND i.invoice_date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND i.invoice_date <= ?"
        params.append(date_to)
    sql += " ORDER BY i.id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@router.get("/invoices/{iid}")
def get_invoice(iid: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM invoices WHERE id=?", (iid,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(404)
    result = dict(row)
    rels = conn.execute("SELECT * FROM invoice_relations WHERE invoice_id=?", (iid,)).fetchall()
    result['relations'] = [dict(r) for r in rels]
    conn.close()
    return result

@router.post("/invoices")
def create_invoice(data: InvoiceModel):
    conn = get_conn()
    try:
        assert_dates_open(conn, data.invoice_date, label="发票")
        pname = ""
        if data.partner_id:
            p = conn.execute("SELECT name FROM partners WHERE id=?", (data.partner_id,)).fetchone()
            pname = p['name'] if p else ""
        conn.execute("""INSERT INTO invoices(invoice_no,invoice_code,invoice_type,direction,project_id,
            partner_id,partner_name,invoice_date,tax_rate,untax_amount,tax_amount,total_amount,status,remark,image_path,ocr_raw)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (data.invoice_no,data.invoice_code,data.invoice_type,data.direction,data.project_id or 0,
             data.partner_id,pname or data.invoice_no,data.invoice_date,data.tax_rate,
             data.untax_amount,data.tax_amount,data.total_amount,data.status,data.remark,
             data.image_path or "", data.ocr_raw or ""))
        iid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # 选单开具：把发票关联到对应出入库单据（用于待开票核销）
        for rel in (data.relations or []):
            if not isinstance(rel, dict):
                continue
            conn.execute("""INSERT INTO invoice_relations(invoice_id,order_type,order_id,order_no,amount)
                VALUES(?,?,?,?,?)""",
                (iid, rel.get('order_type', ''), rel.get('order_id', 0),
                 rel.get('order_no', ''), rel.get('amount', 0)))
        conn.commit()
        return {"ok": True, "id": iid}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()

def _invoice_guard(conn, iid, allow):
    row = conn.execute("SELECT audit_status FROM invoices WHERE id=?", (iid,)).fetchone()
    if not row:
        raise HTTPException(404, "发票不存在")
    if row['audit_status'] not in allow:
        raise HTTPException(400, "该发票已审核/已作废，不能修改。请先反审核或作废后再操作。")
    return row

@router.post("/invoices/{iid}/approve")
def approve_invoice(iid: int):
    conn = get_conn()
    row = _invoice_guard(conn, iid, (0,))
    assert_dates_open(conn, row['invoice_date'], label="发票")
    conn.execute("UPDATE invoices SET audit_status=1, updated_at=datetime('now','localtime') WHERE id=?", (iid,))
    conn.commit(); conn.close(); return {"ok": True}

@router.post("/invoices/{iid}/unapprove")
def unapprove_invoice(iid: int):
    conn = get_conn()
    row = _invoice_guard(conn, iid, (1,))
    assert_dates_open(conn, row['invoice_date'], label="发票")
    conn.execute("UPDATE invoices SET audit_status=0, updated_at=datetime('now','localtime') WHERE id=?", (iid,))
    conn.commit(); conn.close(); return {"ok": True}

@router.post("/invoices/{iid}/void")
def void_invoice(iid: int):
    conn = get_conn()
    row = _invoice_guard(conn, iid, (0, 1))
    assert_dates_open(conn, row['invoice_date'], label="发票")
    conn.execute("UPDATE invoices SET audit_status=2, updated_at=datetime('now','localtime') WHERE id=?", (iid,))
    conn.commit(); conn.close(); return {"ok": True}

@router.post("/invoices/upload")
async def upload_invoice_image(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    """接收发票图片：保存到磁盘并自动识别，返回图片路径 + 解析出的字段。"""
    try:
        content = await file.read()
        rel = save_upload(file.filename or "image.png", content)
        info = recognize_invoice_file(rel)
        info["image_path"] = rel
        return info
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e))

@router.get("/invoices/image/{iid}")
def invoice_image(iid: int):
    """按发票 id 返回其图片文件（用于前端预览）。"""
    conn = get_conn()
    row = conn.execute("SELECT image_path FROM invoices WHERE id=?", (iid,)).fetchone()
    conn.close()
    if not row or not row["image_path"]:
        raise HTTPException(404, "该发票无图片")
    full = os.path.normpath(os.path.join(get_data_dir(), row["image_path"]))
    base = os.path.normpath(get_data_dir())
    if not (full == base or full.startswith(base + os.sep)):
        raise HTTPException(400, "非法路径")
    if not os.path.exists(full):
        raise HTTPException(404, "图片文件缺失")
    return FileResponse(full)

@router.put("/invoices/{iid}")
def update_invoice(iid: int, data: InvoiceModel):
    conn = get_conn()
    _invoice_guard(conn, iid, (0,))
    assert_dates_open(conn, data.invoice_date, label="发票")
    pname = ""
    if data.partner_id:
        p = conn.execute("SELECT name FROM partners WHERE id=?", (data.partner_id,)).fetchone()
        pname = p['name'] if p else ""
    conn.execute("""UPDATE invoices SET invoice_no=?,invoice_code=?,invoice_type=?,direction=?,project_id=?,
        partner_id=?,partner_name=?,invoice_date=?,tax_rate=?,untax_amount=?,tax_amount=?,total_amount=?,
        status=?,remark=?,image_path=?,ocr_raw=?,updated_at=datetime('now','localtime') WHERE id=?""",
        (data.invoice_no,data.invoice_code,data.invoice_type,data.direction,data.project_id or 0,
         data.partner_id,pname,data.invoice_date,data.tax_rate,
         data.untax_amount,data.tax_amount,data.total_amount,data.status,data.remark,
         data.image_path or "", data.ocr_raw or "", iid))
    conn.commit(); conn.close()
    return {"ok": True}

@router.delete("/invoices/{iid}")
def delete_invoice(iid: int):
    conn = get_conn()
    row = _invoice_guard(conn, iid, (0,))
    assert_dates_open(conn, row['invoice_date'], label="发票")
    conn.execute("UPDATE invoices SET status='已作废', audit_status=2 WHERE id=?", (iid,))
    conn.commit(); conn.close()
    return {"ok": True}

@router.post("/invoices/{iid}/relate")
def relate_invoice(iid: int, data: dict):
    conn = get_conn()
    conn.execute("""INSERT INTO invoice_relations(invoice_id,order_type,order_id,order_no,amount)
        VALUES(?,?,?,?,?)""",
        (iid, data.get('order_type',''), data.get('order_id',0),
         data.get('order_no',''), data.get('amount',0)))
    conn.commit(); conn.close()
    return {"ok": True}

@router.get("/orders/pending-invoice")
def pending_invoice_orders(order_type: str = "all", keyword: str = ""):
    """查询需要开票但尚未完全开票的单据（入库单/出库单）"""
    conn = get_conn()
    results = []

    # 采购单（进项）
    if order_type in ("all", "purchase"):
        sql = """
            SELECT o.id, o.order_no, 'purchase' as order_type, '采购入库' as order_type_name,
                   o.order_date, o.total_amount, o.invoice_need, o.invoice_type_req,
                   o.status as order_status, o.remark,
                   o.supplier_id as partner_id,
                   COALESCE(s.name,'') as partner_name,
                   COALESCE(p.name,'') as project_name,
                   COALESCE((SELECT SUM(ir.amount) FROM invoice_relations ir
                       INNER JOIN invoices iv ON ir.invoice_id=iv.id
                       WHERE ir.order_id=o.id AND ir.order_type='purchase'
                       AND iv.status != '已作废'),0) as invoiced_amount
            FROM purchase_orders o
            LEFT JOIN partners s ON o.supplier_id=s.id
            LEFT JOIN projects p ON o.project_id=p.id
            WHERE o.invoice_need=1
        """
        params = []
        if keyword:
            sql += " AND (o.order_no LIKE ? OR s.name LIKE ?)"
            params += [f"%{keyword}%", f"%{keyword}%"]
        sql += " ORDER BY o.id DESC"
        rows = conn.execute(sql, params).fetchall()
        results.extend([dict(r) for r in rows])

    # 销售单（出项）
    if order_type in ("all", "sale"):
        sql = """
            SELECT o.id, o.order_no, 'sale' as order_type, '销售出库' as order_type_name,
                   o.order_date, o.total_amount, o.invoice_need, o.invoice_type_req,
                   o.status as order_status, o.remark,
                   o.customer_id as partner_id,
                   COALESCE(c.name,'') as partner_name,
                   COALESCE(p.name,'') as project_name,
                   COALESCE((SELECT SUM(ir.amount) FROM invoice_relations ir
                       INNER JOIN invoices iv ON ir.invoice_id=iv.id
                       WHERE ir.order_id=o.id AND ir.order_type='sale'
                       AND iv.status != '已作废'),0) as invoiced_amount
            FROM sale_orders o
            LEFT JOIN partners c ON o.customer_id=c.id
            LEFT JOIN projects p ON o.project_id=p.id
            WHERE o.invoice_need=1
        """
        params = []
        if keyword:
            sql += " AND (o.order_no LIKE ? OR c.name LIKE ?)"
            params += [f"%{keyword}%", f"%{keyword}%"]
        sql += " ORDER BY o.id DESC"
        rows = conn.execute(sql, params).fetchall()
        results.extend([dict(r) for r in rows])

    conn.close()
    # 计算剩余未开票金额
    for r in results:
        r['remaining_amount'] = round(r['total_amount'] - r.get('invoiced_amount', 0), 2)
    # 仅保留仍有未开金额的订单（真正待开），与工作台"待开票"口径一致
    results = [r for r in results if r['remaining_amount'] > 0.001]
    # 排序：先按剩余金额降序
    results.sort(key=lambda x: -x['remaining_amount'])
    return results

# ══════════════════════════════════════════
# 统计报表
# ══════════════════════════════════════════
@router.get("/stats/dashboard")
def dashboard():
    conn = get_conn()
    today = datetime.date.today().strftime('%Y-%m-%d')
    month_start = datetime.date.today().replace(day=1).strftime('%Y-%m-%d')

    r = {}
    r['purchase_month'] = conn.execute(
        "SELECT COALESCE(SUM(total_amount),0) v FROM purchase_orders WHERE order_date>=? AND status>=2",
        (month_start,)).fetchone()['v']
    r['sale_month'] = conn.execute(
        "SELECT COALESCE(SUM(total_amount),0) v FROM sale_orders WHERE order_date>=? AND status>=2",
        (month_start,)).fetchone()['v']
    r['profit_month'] = conn.execute(
        "SELECT COALESCE(SUM(gross_profit),0) v FROM sale_orders WHERE order_date>=? AND status>=2",
        (month_start,)).fetchone()['v']
    r['stock_alert'] = conn.execute(
        "SELECT COUNT(*) v FROM inventory i JOIN goods g ON i.goods_id=g.id WHERE i.qty<=g.stock_min AND g.stock_min>0"
    ).fetchone()['v']
    r['pending_purchase'] = conn.execute(
        "SELECT COUNT(*) v FROM purchase_orders WHERE status IN (0,1,2)").fetchone()['v']
    r['pending_sale'] = conn.execute(
        "SELECT COUNT(*) v FROM sale_orders WHERE status IN (0,1,2)").fetchone()['v']
    r['project_count'] = conn.execute(
        "SELECT COUNT(*) v FROM projects WHERE status=1").fetchone()['v']
    # 待开票单据数：订单标记需要开票(invoice_need=1)且仍有未开金额(remaining>0)
    r['invoice_count'] = conn.execute("""
        SELECT COUNT(*) v FROM (
            SELECT o.id, o.total_amount - COALESCE((
                SELECT SUM(ir.amount) FROM invoice_relations ir
                INNER JOIN invoices iv ON ir.invoice_id=iv.id
                WHERE ir.order_id=o.id AND ir.order_type='purchase' AND iv.status != '已作废'),0) AS remaining
            FROM purchase_orders o WHERE o.invoice_need=1
            UNION ALL
            SELECT o.id, o.total_amount - COALESCE((
                SELECT SUM(ir.amount) FROM invoice_relations ir
                INNER JOIN invoices iv ON ir.invoice_id=iv.id
                WHERE ir.order_id=o.id AND ir.order_type='sale' AND iv.status != '已作废'),0) AS remaining
            FROM sale_orders o WHERE o.invoice_need=1
        ) t WHERE t.remaining > 0.001
    """).fetchone()['v']
    # 最近入库
    r['recent_purchase'] = [dict(x) for x in conn.execute(
        """SELECT o.order_no,o.order_date,o.total_amount,COALESCE(s.name,'') supplier_name
           FROM purchase_orders o LEFT JOIN partners s ON o.supplier_id=s.id
           WHERE o.status>=2 ORDER BY o.id DESC LIMIT 5""").fetchall()]
    # 最近出库
    r['recent_sale'] = [dict(x) for x in conn.execute(
        """SELECT o.order_no,o.order_date,o.total_amount,o.gross_profit,COALESCE(c.name,'') customer_name
           FROM sale_orders o LEFT JOIN partners c ON o.customer_id=c.id
           WHERE o.status>=2 ORDER BY o.id DESC LIMIT 5""").fetchall()]
    conn.close()
    return r

@router.get("/stats/inventory_value")
def inventory_value():
    conn = get_conn()
    rows = conn.execute("""SELECT g.name,g.unit,SUM(i.qty) total_qty,SUM(i.total_cost) total_value
        FROM inventory i JOIN goods g ON i.goods_id=g.id
        GROUP BY i.goods_id HAVING total_qty>0 ORDER BY total_value DESC""").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@router.get("/stats/project_cost")
def project_cost():
    conn = get_conn()
    rows = conn.execute("""SELECT p.name, p.code,
        COALESCE((SELECT SUM(total_amount) FROM purchase_orders WHERE project_id=p.id AND status>=2),0) purchase_total,
        COALESCE((SELECT SUM(untax_amount) FROM sale_orders WHERE project_id=p.id AND status>=2),0) sale_total,
        COALESCE((SELECT SUM(gross_profit) FROM sale_orders WHERE project_id=p.id AND status>=2),0) gross_profit,
        COALESCE((SELECT SUM(total_cost) FROM requisitions WHERE project_id=p.id AND status>=2),0) req_total
        FROM projects p WHERE p.status=1 ORDER BY p.id""").fetchall()
    conn.close()
    return [dict(r) for r in rows]
