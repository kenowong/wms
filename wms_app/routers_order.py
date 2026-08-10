# -*- coding: utf-8 -*-
"""API路由 - 采购、销售、耗材领用"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
from database import get_conn
from period_guard import assert_dates_open
from routers_auth import get_current_user
import datetime

router = APIRouter()

def gen_no(prefix: str, conn) -> str:
    """生成单据编号，各前缀独立计数，格式 PREFIX-YYYYMMDD-NNNN"""
    today = datetime.date.today().strftime('%Y%m%d')
    pat = f"{prefix}-{today}-%"
    # 根据前缀查对应表
    table_map = {
        "PO": "purchase_orders",
        "SO": "sale_orders",
        "MR": "requisitions",
        "IC": "inventory_checks",
    }
    no_col_map = {
        "PO": "order_no",
        "SO": "order_no",
        "MR": "order_no",
        "IC": "check_no",
    }
    table = table_map.get(prefix)
    col = no_col_map.get(prefix, "order_no")
    if table:
        row = conn.execute(f"SELECT {col} FROM {table} WHERE {col} LIKE ? ORDER BY {col} DESC LIMIT 1", (pat,)).fetchone()
    else:
        row = None
    if row:
        try:
            seq = int(row[0].split('-')[-1]) + 1
        except Exception:
            seq = 1
    else:
        seq = 1
    return f"{prefix}-{today}-{seq:04d}"

def update_inventory(conn, goods_id, warehouse_id, project_id, direction, qty, cost_price, biz_type, biz_no, operator=""):
    """更新库存台账并写流水"""
    pid = project_id or 0
    row = conn.execute(
        "SELECT qty,avg_cost,total_cost FROM inventory WHERE goods_id=? AND warehouse_id=? AND project_id=?",
        (goods_id, warehouse_id, pid)).fetchone()
    
    g = conn.execute("SELECT name FROM goods WHERE id=?", (goods_id,)).fetchone()
    w = conn.execute("SELECT name FROM warehouses WHERE id=?", (warehouse_id,)).fetchone()
    p = conn.execute("SELECT name FROM projects WHERE id=?", (pid,)).fetchone() if pid else None

    goods_name = g['name'] if g else ''
    wh_name = w['name'] if w else ''
    proj_name = p['name'] if p else ''

    if row:
        before_qty = row['qty']
        if direction == 1:  # 入库：更新加权平均成本
            new_qty = row['qty'] + qty
            new_total = row['total_cost'] + qty * cost_price
            new_avg = new_total / new_qty if new_qty > 0 else cost_price
        else:  # 出库
            new_qty = row['qty'] - qty
            if new_qty < -0.001:
                raise HTTPException(400, f"商品[{goods_name}]库存不足，当前库存:{row['qty']:.2f}")
            new_total = new_qty * row['avg_cost']
            new_avg = row['avg_cost']
        conn.execute(
            "UPDATE inventory SET qty=?,avg_cost=?,total_cost=?,updated_at=datetime('now','localtime') "
            "WHERE goods_id=? AND warehouse_id=? AND project_id=?",
            (new_qty, new_avg, new_total, goods_id, warehouse_id, pid))
    else:
        if direction == -1 and qty > 0:
            raise HTTPException(400, f"商品[{goods_name}]无库存记录")
        before_qty = 0
        new_qty = qty if direction == 1 else 0
        new_avg = cost_price
        new_total = new_qty * cost_price
        conn.execute(
            "INSERT INTO inventory(goods_id,warehouse_id,project_id,qty,avg_cost,total_cost) VALUES(?,?,?,?,?,?)",
            (goods_id, warehouse_id, pid, new_qty, new_avg, new_total))

    conn.execute("""INSERT INTO inventory_logs(goods_id,goods_name,warehouse_id,warehouse_name,project_id,project_name,
        biz_type,biz_order_no,direction,qty,before_qty,after_qty,cost_price,operator)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (goods_id,goods_name,warehouse_id,wh_name,pid,proj_name,
         biz_type,biz_no,direction,qty,before_qty,new_qty,cost_price,operator))

# ══════════════════════════════════════════
# 采购单
# ══════════════════════════════════════════
class PurchaseItemIn(BaseModel):
    goods_id: int
    qty: float
    unit_price: float
    tax_rate: Optional[float] = 13.0
    spec: Optional[str] = ""
    model: Optional[str] = ""
    unit: Optional[str] = ""
    remark: Optional[str] = ""

class PurchaseOrderIn(BaseModel):
    project_id: Optional[int] = None
    supplier_id: Optional[int] = None
    warehouse_id: int
    order_date: Optional[str] = ""
    business_person: Optional[str] = ""    # 业务人员（可下拉选择，执行人由后端从登录用户写入）
    remark: Optional[str] = ""
    invoice_need: Optional[int] = 0        # 0无需 1需要
    invoice_type_req: Optional[str] = ""   # 增值税发票/普通发票/收据
    fee: Optional[float] = 0               # 手续费（计入公司费用）
    items: List[PurchaseItemIn]

@router.get("/purchase")
def list_purchase(keyword: str = "", status: int = -1, project_id: int = -1,
                  operator: str = "", business_person: str = ""):
    conn = get_conn()
    sql = """SELECT o.*,
        COALESCE(s.name,'') as supplier_name,
        COALESCE(p.name,'') as project_name,
        COALESCE(w.name,'') as warehouse_name
        FROM purchase_orders o
        LEFT JOIN partners s ON o.supplier_id=s.id
        LEFT JOIN projects p ON o.project_id=p.id
        LEFT JOIN warehouses w ON o.warehouse_id=w.id
        WHERE 1=1"""
    params = []
    if keyword:
        sql += " AND (o.order_no LIKE ? OR s.name LIKE ?)"
        params += [f"%{keyword}%",f"%{keyword}%"]
    if status >= 0:
        sql += " AND o.status=?"
        params.append(status)
    if project_id >= 0:
        sql += " AND o.project_id=?"
        params.append(project_id)
    if operator:
        sql += " AND o.operator=?"
        params.append(operator)
    if business_person:
        sql += " AND o.business_person=?"
        params.append(business_person)
    sql += " ORDER BY o.id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@router.get("/purchase/{oid}")
def get_purchase(oid: int):
    conn = get_conn()
    row = conn.execute("""SELECT o.*,
        COALESCE(s.name,'') as supplier_name,
        COALESCE(p.name,'') as project_name,
        COALESCE(w.name,'') as warehouse_name
        FROM purchase_orders o
        LEFT JOIN partners s ON o.supplier_id=s.id
        LEFT JOIN projects p ON o.project_id=p.id
        LEFT JOIN warehouses w ON o.warehouse_id=w.id
        WHERE o.id=?""", (oid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404)
    result = dict(row)
    items = conn.execute("""SELECT i.*, g.name as goods_name_ref FROM purchase_items i
        LEFT JOIN goods g ON i.goods_id=g.id WHERE i.order_id=?""", (oid,)).fetchall()
    result['items'] = [dict(i) for i in items]
    conn.close()
    return result

@router.post("/purchase")
def create_purchase(data: PurchaseOrderIn, user: dict = Depends(get_current_user)):
    conn = get_conn()
    try:
        assert_dates_open(conn, data.order_date, label="采购单")
        order_no = gen_no("PO", conn)
        total = sum(i.qty * i.unit_price for i in data.items)
        tax = sum(i.qty * i.unit_price * (i.tax_rate/100) / (1 + i.tax_rate/100) for i in data.items)
        conn.execute("""INSERT INTO purchase_orders(order_no,project_id,supplier_id,warehouse_id,
            order_date,total_amount,tax_amount,untax_amount,status,invoice_need,invoice_type_req,operator,business_person,remark,fee)
            VALUES(?,?,?,?,?,?,?,?,0,?,?,?,?,?,?)""",
            (order_no,data.project_id,data.supplier_id,data.warehouse_id,
             data.order_date,total,tax,total-tax,
             data.invoice_need or 0, data.invoice_type_req or '',
             user['display_name'], data.business_person or '', data.remark, data.fee or 0))
        oid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for i in data.items:
            g = conn.execute("SELECT name,spec,model,unit FROM goods WHERE id=?", (i.goods_id,)).fetchone()
            t_amt = i.qty * i.unit_price
            t_tax = t_amt * (i.tax_rate/100) / (1 + i.tax_rate/100)
            conn.execute("""INSERT INTO purchase_items(order_id,goods_id,goods_name,spec,model,unit,qty,unit_price,tax_rate,tax_amount,untax_amount,total_amount,remark)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (oid, i.goods_id, g['name'] if g else '', i.spec or (g['spec'] if g else ''),
                 i.model or (g['model'] if g else ''),
                 i.unit or (g['unit'] if g else ''), i.qty, i.unit_price, i.tax_rate,
                 t_tax, t_amt-t_tax, t_amt, i.remark))
        conn.commit()
        return {"ok": True, "order_no": order_no}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()

@router.post("/purchase/{oid}/submit")
def submit_purchase(oid: int):
    conn = get_conn()
    conn.execute("UPDATE purchase_orders SET status=1,updated_at=datetime('now','localtime') WHERE id=? AND status=0",(oid,))
    conn.commit(); conn.close()
    return {"ok": True}

@router.post("/purchase/{oid}/approve")
def approve_purchase(oid: int):
    conn = get_conn()
    conn.execute("UPDATE purchase_orders SET status=2,updated_at=datetime('now','localtime') WHERE id=? AND status=1",(oid,))
    conn.commit(); conn.close()
    return {"ok": True}

@router.post("/purchase/{oid}/instock")
def instock_purchase(oid: int, user: dict = Depends(get_current_user), body: dict = {}):
    """确认入库"""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM purchase_orders WHERE id=?", (oid,)).fetchone()
        if not row: raise HTTPException(404)
        if row['status'] != 2: raise HTTPException(400, "请先审核后再入库")
        assert_dates_open(conn, row['order_date'], label="采购单")
        items = conn.execute("SELECT * FROM purchase_items WHERE order_id=?", (oid,)).fetchall()
        for item in items:
            cost_price = item['unit_price'] / (1 + item['tax_rate']/100)  # 不含税单价作为成本
            update_inventory(conn, item['goods_id'], row['warehouse_id'], row['project_id'],
                           1, item['qty'], cost_price, 'purchase', row['order_no'], user['display_name'])
        conn.execute("UPDATE purchase_orders SET status=3,updated_at=datetime('now','localtime') WHERE id=?",(oid,))
        conn.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(400, str(e))
    finally:
        conn.close()

@router.delete("/purchase/{oid}")
def delete_purchase(oid: int):
    conn = get_conn()
    row = conn.execute("SELECT status,order_date FROM purchase_orders WHERE id=?", (oid,)).fetchone()
    if row:
        assert_dates_open(conn, row['order_date'], label="采购单")
    if row and row['status'] >= 3:
        raise HTTPException(400, "已入库单据不能删除")
    conn.execute("DELETE FROM purchase_items WHERE order_id=?", (oid,))
    conn.execute("DELETE FROM purchase_orders WHERE id=?", (oid,))
    conn.commit(); conn.close()
    return {"ok": True}

# ══════════════════════════════════════════
# 销售单
# ══════════════════════════════════════════
class SaleItemIn(BaseModel):
    goods_id: int
    qty: float
    unit_price: float
    tax_rate: Optional[float] = 13.0
    spec: Optional[str] = ""
    model: Optional[str] = ""
    unit: Optional[str] = ""
    remark: Optional[str] = ""

class SaleOrderIn(BaseModel):
    project_id: Optional[int] = None
    customer_id: Optional[int] = None
    warehouse_id: int
    order_date: Optional[str] = ""
    business_person: Optional[str] = ""    # 业务人员（可下拉选择，执行人由后端从登录用户写入）
    remark: Optional[str] = ""
    invoice_need: Optional[int] = 0        # 0无需 1需要
    invoice_type_req: Optional[str] = ""   # 增值税发票/普通发票/收据
    fee: Optional[float] = 0               # 手续费（计入公司费用）
    items: List[SaleItemIn]

@router.get("/sale")
def list_sale(keyword: str = "", status: int = -1, project_id: int = -1,
              operator: str = "", business_person: str = ""):
    conn = get_conn()
    sql = """SELECT o.*,
        COALESCE(c.name,'') as customer_name,
        COALESCE(p.name,'') as project_name,
        COALESCE(w.name,'') as warehouse_name
        FROM sale_orders o
        LEFT JOIN partners c ON o.customer_id=c.id
        LEFT JOIN projects p ON o.project_id=p.id
        LEFT JOIN warehouses w ON o.warehouse_id=w.id
        WHERE 1=1"""
    params = []
    if keyword:
        sql += " AND (o.order_no LIKE ? OR c.name LIKE ?)"
        params += [f"%{keyword}%",f"%{keyword}%"]
    if status >= 0:
        sql += " AND o.status=?"
        params.append(status)
    if project_id >= 0:
        sql += " AND o.project_id=?"
        params.append(project_id)
    if operator:
        sql += " AND o.operator=?"
        params.append(operator)
    if business_person:
        sql += " AND o.business_person=?"
        params.append(business_person)
    sql += " ORDER BY o.id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@router.get("/sale/{oid}")
def get_sale(oid: int):
    conn = get_conn()
    row = conn.execute("""SELECT o.*,
        COALESCE(c.name,'') as customer_name,
        COALESCE(p.name,'') as project_name,
        COALESCE(w.name,'') as warehouse_name
        FROM sale_orders o
        LEFT JOIN partners c ON o.customer_id=c.id
        LEFT JOIN projects p ON o.project_id=p.id
        LEFT JOIN warehouses w ON o.warehouse_id=w.id
        WHERE o.id=?""", (oid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404)
    result = dict(row)
    items = conn.execute("SELECT * FROM sale_items WHERE order_id=?", (oid,)).fetchall()
    result['items'] = [dict(i) for i in items]
    conn.close()
    return result

@router.post("/sale")
def create_sale(data: SaleOrderIn, user: dict = Depends(get_current_user)):
    conn = get_conn()
    try:
        assert_dates_open(conn, data.order_date, label="销售单")
        order_no = gen_no("SO", conn)
        total = sum(i.qty * i.unit_price for i in data.items)
        tax = sum(i.qty * i.unit_price * (i.tax_rate/100) / (1 + i.tax_rate/100) for i in data.items)
        conn.execute("""INSERT INTO sale_orders(order_no,project_id,customer_id,warehouse_id,
            order_date,total_amount,tax_amount,untax_amount,status,invoice_need,invoice_type_req,operator,business_person,remark,fee)
            VALUES(?,?,?,?,?,?,?,?,0,?,?,?,?,?,?)""",
            (order_no,data.project_id,data.customer_id,data.warehouse_id,
             data.order_date,total,tax,total-tax,
             data.invoice_need or 0, data.invoice_type_req or '',
             user['display_name'], data.business_person or '', data.remark, data.fee or 0))
        oid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for i in data.items:
            g = conn.execute("SELECT name,spec,model,unit FROM goods WHERE id=?", (i.goods_id,)).fetchone()
            t_amt = i.qty * i.unit_price
            conn.execute("""INSERT INTO sale_items(order_id,goods_id,goods_name,spec,model,unit,qty,unit_price,tax_rate,total_amount,remark)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (oid,i.goods_id,g['name'] if g else '',i.spec or (g['spec'] if g else ''),
                 i.model or (g['model'] if g else ''),
                 i.unit or (g['unit'] if g else ''),i.qty,i.unit_price,i.tax_rate,t_amt,i.remark))
        conn.commit()
        return {"ok": True, "order_no": order_no}
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()

@router.post("/sale/{oid}/submit")
def submit_sale(oid: int):
    conn = get_conn()
    conn.execute("UPDATE sale_orders SET status=1 WHERE id=? AND status=0",(oid,))
    conn.commit(); conn.close()
    return {"ok": True}

@router.post("/sale/{oid}/approve")
def approve_sale(oid: int):
    conn = get_conn()
    conn.execute("UPDATE sale_orders SET status=2 WHERE id=? AND status=1",(oid,))
    conn.commit(); conn.close()
    return {"ok": True}

@router.post("/sale/{oid}/outstock")
def outstock_sale(oid: int, user: dict = Depends(get_current_user), body: dict = {}):
    """确认出库"""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM sale_orders WHERE id=?", (oid,)).fetchone()
        if not row: raise HTTPException(404)
        if row['status'] != 2: raise HTTPException(400, "请先审核后再出库")
        assert_dates_open(conn, row['order_date'], label="销售单")
        items = conn.execute("SELECT * FROM sale_items WHERE order_id=?", (oid,)).fetchall()
        total_cost = 0
        for item in items:
            inv = conn.execute(
                "SELECT avg_cost FROM inventory WHERE goods_id=? AND warehouse_id=? AND project_id=?",
                (item['goods_id'], row['warehouse_id'], row['project_id'] or 0)).fetchone()
            cost_price = inv['avg_cost'] if inv else 0
            total_cost += item['qty'] * cost_price
            conn.execute("UPDATE sale_items SET cost_price=? WHERE id=?", (cost_price, item['id']))
            update_inventory(conn, item['goods_id'], row['warehouse_id'], row['project_id'],
                           -1, item['qty'], cost_price, 'sale', row['order_no'], user['display_name'])
        # 毛利必须同口径：收入用不含税(untax_amount)，成本用不含税(avg_cost)，
        # 否则"含税收入-不含税成本"会把销项税额算成利润，毛利虚高。
        gross = row['untax_amount'] - total_cost
        conn.execute("""UPDATE sale_orders SET status=3,cost_amount=?,gross_profit=?,
            updated_at=datetime('now','localtime') WHERE id=?""", (total_cost, gross, oid))
        conn.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(400, str(e))
    finally:
        conn.close()

@router.delete("/sale/{oid}")
def delete_sale(oid: int):
    conn = get_conn()
    row = conn.execute("SELECT status,order_date FROM sale_orders WHERE id=?", (oid,)).fetchone()
    if row:
        assert_dates_open(conn, row['order_date'], label="销售单")
    if row and row['status'] >= 3:
        raise HTTPException(400, "已出库单据不能删除")
    conn.execute("DELETE FROM sale_items WHERE order_id=?", (oid,))
    conn.execute("DELETE FROM sale_orders WHERE id=?", (oid,))
    conn.commit(); conn.close()
    return {"ok": True}

# ══════════════════════════════════════════
# 耗材领用
# ══════════════════════════════════════════
class ReqItemIn(BaseModel):
    goods_id: int
    qty: float
    unit: Optional[str] = ""
    remark: Optional[str] = ""

class ReqOrderIn(BaseModel):
    project_id: int
    warehouse_id: int
    req_date: Optional[str] = ""
    applicant: Optional[str] = ""
    remark: Optional[str] = ""
    items: List[ReqItemIn]

@router.get("/requisition")
def list_req(project_id: int = -1, status: int = -1,
             operator: str = "", business_person: str = ""):
    conn = get_conn()
    sql = """SELECT r.*, p.name as project_name, w.name as warehouse_name
        FROM requisitions r
        LEFT JOIN projects p ON r.project_id=p.id
        LEFT JOIN warehouses w ON r.warehouse_id=w.id WHERE 1=1"""
    params = []
    if project_id >= 0:
        sql += " AND r.project_id=?"
        params.append(project_id)
    if status >= 0:
        sql += " AND r.status=?"
        params.append(status)
    if operator:
        sql += " AND r.operator=?"
        params.append(operator)
    if business_person:
        sql += " AND r.applicant=?"
        params.append(business_person)
    sql += " ORDER BY r.id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@router.get("/requisition/{oid}")
def get_req(oid: int):
    conn = get_conn()
    row = conn.execute("""SELECT r.*, p.name as project_name, w.name as warehouse_name
        FROM requisitions r
        LEFT JOIN projects p ON r.project_id=p.id
        LEFT JOIN warehouses w ON r.warehouse_id=w.id WHERE r.id=?""", (oid,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(404)
    result = dict(row)
    items = conn.execute("SELECT * FROM requisition_items WHERE order_id=?", (oid,)).fetchall()
    result['items'] = [dict(i) for i in items]
    conn.close()
    return result

@router.post("/requisition")
def create_req(data: ReqOrderIn, user: dict = Depends(get_current_user)):
    conn = get_conn()
    try:
        assert_dates_open(conn, data.req_date, label="耗材领用单")
        order_no = gen_no("MR", conn)
        conn.execute("""INSERT INTO requisitions(order_no,project_id,warehouse_id,req_date,applicant,operator,status,remark)
            VALUES(?,?,?,?,?,?,0,?)""",
            (order_no,data.project_id,data.warehouse_id,data.req_date,data.applicant,user['display_name'],data.remark))
        oid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for i in data.items:
            g = conn.execute("SELECT name,unit FROM goods WHERE id=?", (i.goods_id,)).fetchone()
            conn.execute("""INSERT INTO requisition_items(order_id,goods_id,goods_name,unit,qty,remark)
                VALUES(?,?,?,?,?,?)""",
                (oid,i.goods_id,g['name'] if g else '',i.unit or (g['unit'] if g else ''),i.qty,i.remark))
        conn.commit()
        return {"ok": True, "order_no": order_no}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()

@router.post("/requisition/{oid}/outstock")
def outstock_req(oid: int, user: dict = Depends(get_current_user), body: dict = {}):
    """领用出库"""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM requisitions WHERE id=?", (oid,)).fetchone()
        if not row: raise HTTPException(404)
        if row['status'] >= 3: raise HTTPException(400, "已出库")
        assert_dates_open(conn, row['req_date'], label="耗材领用单")
        items = conn.execute("SELECT * FROM requisition_items WHERE order_id=?", (oid,)).fetchall()
        total_cost = 0
        for item in items:
            inv = conn.execute(
                "SELECT avg_cost FROM inventory WHERE goods_id=? AND warehouse_id=? AND project_id=?",
                (item['goods_id'], row['warehouse_id'], row['project_id'])).fetchone()
            cost_price = inv['avg_cost'] if inv else 0
            total_cost += item['qty'] * cost_price
            conn.execute("UPDATE requisition_items SET cost_price=?,total_cost=? WHERE id=?",
                (cost_price, item['qty']*cost_price, item['id']))
            update_inventory(conn, item['goods_id'], row['warehouse_id'], row['project_id'],
                           -1, item['qty'], cost_price, 'requisition', row['order_no'], user['display_name'])
        conn.execute("UPDATE requisitions SET status=3,total_cost=? WHERE id=?", (total_cost, oid))
        conn.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(400, str(e))
    finally:
        conn.close()
