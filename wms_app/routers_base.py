# -*- coding: utf-8 -*-
"""API路由 - 商品、往来单位、仓库、项目"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from database import get_conn
import datetime

router = APIRouter()

# ══════════════════════════════════════════
# 通用编码生成工具
# ══════════════════════════════════════════
def _gen_code(conn, prefix: str, table: str, col: str = "code") -> str:
    """生成 PREFIX-YYYYMMDD-NNNN 格式编码，每个前缀独立计数"""
    today = datetime.date.today().strftime('%Y%m%d')
    pat = f"{prefix}-{today}-%"
    row = conn.execute(f"SELECT {col} FROM {table} WHERE {col} LIKE ? ORDER BY {col} DESC LIMIT 1", (pat,)).fetchone()
    if row:
        try:
            seq = int(row[0].split('-')[-1]) + 1
        except Exception:
            seq = 1
    else:
        seq = 1
    return f"{prefix}-{today}-{seq:04d}"

def _gen_wh_code(conn) -> str:
    """生成仓库编码 WH-NNNN"""
    row = conn.execute("SELECT code FROM warehouses WHERE code LIKE 'WH-%' ORDER BY code DESC LIMIT 1").fetchone()
    if row:
        try:
            seq = int(row[0].split('-')[-1]) + 1
        except Exception:
            seq = 1
    else:
        seq = 1
    return f"WH-{seq:04d}"

@router.get("/gen_code")
def get_gen_code(type: str):
    """前端调用：获取自动编码  type= goods|supplier|customer|partner|project|warehouse"""
    conn = get_conn()
    try:
        if type == "goods":
            return {"code": _gen_code(conn, "G", "goods")}
        elif type in ("supplier", "customer", "partner"):
            prefix = "SP" if type == "supplier" else ("CU" if type == "customer" else "BP")
            return {"code": _gen_code(conn, prefix, "partners")}
        elif type == "project":
            return {"code": _gen_code(conn, "PRJ", "projects")}
        elif type == "warehouse":
            return {"code": _gen_wh_code(conn)}
        else:
            raise HTTPException(400, f"未知类型: {type}")
    finally:
        conn.close()

# ══════════════════════════════════════════
# 商品档案
# ══════════════════════════════════════════
class GoodsModel(BaseModel):
    code: Optional[str] = ""
    name: str
    spec: Optional[str] = ""
    model: Optional[str] = ""
    barcode: Optional[str] = ""
    brand_id: Optional[int] = 0
    unit: Optional[str] = "个"
    unit2: Optional[str] = ""
    unit2_rate: Optional[float] = 0
    category_id: Optional[int] = 0
    default_tax_rate: Optional[float] = 13.0
    ref_purchase_price: Optional[float] = 0
    ref_sale_price: Optional[float] = 0
    stock_min: Optional[float] = 0
    stock_max: Optional[float] = 99999
    status: Optional[int] = 1
    remark: Optional[str] = ""

@router.get("/goods")
def list_goods(keyword: str = "", status: int = -1, category_id: int = -1, brand_id: int = -1):
    conn = get_conn()
    sql = """SELECT g.*,
        c.name as category_name,
        b.name as brand_name,
        c1.name as cat1_name, c2.name as cat2_name, c3.name as cat3_name
        FROM goods g
        LEFT JOIN categories c ON g.category_id=c.id
        LEFT JOIN brands b ON g.brand_id=b.id
        LEFT JOIN categories c3 ON c3.id=g.category_id
        LEFT JOIN categories c2 ON c2.id=c3.parent_id
        LEFT JOIN categories c1 ON c1.id=c2.parent_id
        WHERE 1=1"""
    params = []
    if keyword:
        sql += " AND (g.name LIKE ? OR g.code LIKE ? OR g.spec LIKE ? OR g.model LIKE ? OR g.barcode LIKE ?)"
        params += [f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"]
    if status >= 0:
        sql += " AND g.status=?"
        params.append(status)
    if category_id >= 0:
        # 支持上级分类过滤（含所有子类）
        sql += " AND (g.category_id=? OR c3.parent_id=? OR c2.parent_id=?)"
        params += [category_id, category_id, category_id]
    if brand_id >= 0:
        sql += " AND g.brand_id=?"
        params.append(brand_id)
    sql += " ORDER BY g.id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        # 构建分类路径
        parts = [d.get('cat1_name'), d.get('cat2_name'), d.get('cat3_name')]
        d['category_path'] = ' > '.join(p for p in parts if p)
        result.append(d)
    return result

@router.get("/goods/scan")
def scan_goods(code: str = ""):
    """按条码（或商品编码）解析商品，供扫码枪录入使用（须定义在 /goods/{gid} 之前）"""
    if not code:
        raise HTTPException(404, "未找到对应商品")
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM goods WHERE barcode=?", (code,)).fetchone()
        if not row:
            row = conn.execute("SELECT * FROM goods WHERE code=?", (code,)).fetchone()
        if not row:
            row = conn.execute("SELECT * FROM goods WHERE barcode LIKE ?", (f"%{code}%",)).fetchone()
        if not row:
            conn.close()
            raise HTTPException(404, "未找到对应商品")
        conn.close()
        return dict(row)
    except HTTPException:
        raise
    except Exception as e:
        conn.close()
        raise HTTPException(400, str(e))

@router.get("/goods/{gid}")
def get_goods(gid: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM goods WHERE id=?", (gid,)).fetchone()
    conn.close()
    if not row: raise HTTPException(404, "商品不存在")
    return dict(row)

@router.post("/goods")
def create_goods(g: GoodsModel):
    conn = get_conn()
    try:
        code = g.code.strip() if g.code and g.code.strip() else _gen_code(conn, "G", "goods")
        conn.execute("""INSERT INTO goods(code,name,spec,model,barcode,brand_id,unit,unit2,unit2_rate,category_id,default_tax_rate,
            ref_purchase_price,ref_sale_price,stock_min,stock_max,status,remark)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (code,g.name,g.spec,g.model,g.barcode,g.brand_id,g.unit,g.unit2,g.unit2_rate,g.category_id,g.default_tax_rate,
             g.ref_purchase_price,g.ref_sale_price,g.stock_min,g.stock_max,g.status,g.remark))
        conn.commit()
        return {"ok": True, "code": code}
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()

@router.put("/goods/{gid}")
def update_goods(gid: int, g: GoodsModel):
    conn = get_conn()
    conn.execute("""UPDATE goods SET code=?,name=?,spec=?,model=?,barcode=?,brand_id=?,unit=?,unit2=?,unit2_rate=?,category_id=?,default_tax_rate=?,
        ref_purchase_price=?,ref_sale_price=?,stock_min=?,stock_max=?,status=?,remark=?
        WHERE id=?""",
        (g.code,g.name,g.spec,g.model,g.barcode,g.brand_id,g.unit,g.unit2,g.unit2_rate,g.category_id,g.default_tax_rate,
         g.ref_purchase_price,g.ref_sale_price,g.stock_min,g.stock_max,g.status,g.remark,gid))
    conn.commit()
    conn.close()
    return {"ok": True}

@router.delete("/goods/{gid}")
def delete_goods(gid: int):
    conn = get_conn()
    conn.execute("UPDATE goods SET status=0 WHERE id=?", (gid,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ══════════════════════════════════════════
# 计量单位字典
# ══════════════════════════════════════════
class UnitModel(BaseModel):
    name: str
    remark: Optional[str] = ""

@router.get("/units")
def list_units():
    """计量单位列表"""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM units ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@router.post("/units")
def create_unit(u: UnitModel):
    conn = get_conn()
    try:
        name = u.name.strip()
        if not name:
            raise HTTPException(400, "单位名称不能为空")
        conn.execute("INSERT INTO units(name,remark) VALUES(?,?)", (name, u.remark or ''))
        conn.commit()
        return {"ok": True, "name": name}
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()

@router.delete("/units/{uid}")
def delete_unit(uid: int):
    conn = get_conn()
    try:
        # 禁止删除正在被商品使用的单位
        used = conn.execute(
            "SELECT count(*) FROM goods WHERE unit=(SELECT name FROM units WHERE id=?) OR unit2=(SELECT name FROM units WHERE id=?)",
            (uid, uid)).fetchone()[0]
        if used:
            raise HTTPException(400, "该单位正被商品使用，无法删除")
        conn.execute("DELETE FROM units WHERE id=?", (uid,))
        conn.commit()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()


# ══════════════════════════════════════════
# 往来单位
# ══════════════════════════════════════════
class PartnerModel(BaseModel):
    code: Optional[str] = ""
    name: str
    type: Optional[int] = 3
    contact: Optional[str] = ""
    phone: Optional[str] = ""
    address: Optional[str] = ""
    tax_no: Optional[str] = ""
    bank_name: Optional[str] = ""
    bank_account: Optional[str] = ""
    status: Optional[int] = 1
    remark: Optional[str] = ""
    is_referrer: Optional[int] = 0   # 1=介绍方（可登记提成）

@router.get("/partners")
def list_partners(keyword: str = "", type: int = -1):
    conn = get_conn()
    sql = "SELECT * FROM partners WHERE 1=1"
    params = []
    if keyword:
        sql += " AND (name LIKE ? OR code LIKE ? OR contact LIKE ?)"
        params += [f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"]
    if type >= 0:
        sql += " AND (type=? OR type=3)"
        params.append(type)
    sql += " ORDER BY id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@router.post("/partners")
def create_partner(p: PartnerModel):
    conn = get_conn()
    try:
        # 根据类型决定编码前缀：1=供应商 SP，2=客户 CU，3=两者 BP
        prefix_map = {1: "SP", 2: "CU", 3: "BP"}
        prefix = prefix_map.get(p.type, "BP")
        code = p.code.strip() if p.code and p.code.strip() else _gen_code(conn, prefix, "partners")
        conn.execute("""INSERT INTO partners(code,name,type,contact,phone,address,tax_no,bank_name,bank_account,status,remark,is_referrer)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (code,p.name,p.type,p.contact,p.phone,p.address,p.tax_no,p.bank_name,p.bank_account,p.status,p.remark,
             p.is_referrer or 0))
        conn.commit()
        return {"ok": True, "code": code}
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()

@router.put("/partners/{pid}")
def update_partner(pid: int, p: PartnerModel):
    conn = get_conn()
    conn.execute("""UPDATE partners SET code=?,name=?,type=?,contact=?,phone=?,address=?,tax_no=?,bank_name=?,bank_account=?,status=?,remark=?,is_referrer=?
        WHERE id=?""",
        (p.code,p.name,p.type,p.contact,p.phone,p.address,p.tax_no,p.bank_name,p.bank_account,p.status,p.remark,
         p.is_referrer or 0,pid))
    conn.commit()
    conn.close()
    return {"ok": True}

@router.delete("/partners/{pid}")
def delete_partner(pid: int):
    conn = get_conn()
    conn.execute("UPDATE partners SET status=0 WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return {"ok": True}

# ══════════════════════════════════════════
# 仓库
# ══════════════════════════════════════════
@router.get("/warehouses")
def list_warehouses():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM warehouses ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@router.post("/warehouses")
def create_warehouse(data: dict):
    conn = get_conn()
    code = (data.get('code') or '').strip() or _gen_wh_code(conn)
    conn.execute("INSERT INTO warehouses(code,name,manager,remark) VALUES(?,?,?,?)",
        (code, data.get('name',''), data.get('manager',''), data.get('remark','')))
    conn.commit()
    conn.close()
    return {"ok": True, "code": code}

# ══════════════════════════════════════════
# 项目
# ══════════════════════════════════════════
class ProjectModel(BaseModel):
    code: Optional[str] = ""
    name: str
    customer_id: Optional[int] = None
    manager: Optional[str] = ""
    start_date: Optional[str] = ""
    end_date: Optional[str] = ""
    contract_amount: Optional[float] = 0
    status: Optional[int] = 1
    remark: Optional[str] = ""

@router.get("/projects")
def list_projects(keyword: str = "", status: int = -1):
    conn = get_conn()
    sql = """SELECT p.*, c.name as customer_name FROM projects p
             LEFT JOIN partners c ON p.customer_id=c.id WHERE 1=1"""
    params = []
    if keyword:
        sql += " AND (p.name LIKE ? OR p.code LIKE ?)"
        params += [f"%{keyword}%", f"%{keyword}%"]
    if status >= 0:
        sql += " AND p.status=?"
        params.append(status)
    sql += " ORDER BY p.id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@router.get("/projects/{pid}")
def get_project(pid: int):
    conn = get_conn()
    row = conn.execute("""SELECT p.*, c.name as customer_name FROM projects p
        LEFT JOIN partners c ON p.customer_id=c.id WHERE p.id=?""", (pid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "项目不存在")
    result = dict(row)
    # 项目统计
    result['purchase_total'] = conn.execute(
        "SELECT COALESCE(SUM(total_amount),0) FROM purchase_orders WHERE project_id=? AND status>=2",(pid,)).fetchone()[0]
    result['sale_total'] = conn.execute(
        "SELECT COALESCE(SUM(total_amount),0) FROM sale_orders WHERE project_id=? AND status>=2",(pid,)).fetchone()[0]
    result['req_total'] = conn.execute(
        "SELECT COALESCE(SUM(total_cost),0) FROM requisitions WHERE project_id=? AND status>=2",(pid,)).fetchone()[0]
    conn.close()
    return result

@router.post("/projects")
def create_project(p: ProjectModel):
    conn = get_conn()
    try:
        code = p.code.strip() if p.code and p.code.strip() else _gen_code(conn, "PRJ", "projects")
        conn.execute("""INSERT INTO projects(code,name,customer_id,manager,start_date,end_date,contract_amount,status,remark)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (code,p.name,p.customer_id,p.manager,p.start_date,p.end_date,p.contract_amount,p.status,p.remark))
        conn.commit()
        return {"ok": True, "code": code}
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()

@router.put("/projects/{pid}")
def update_project(pid: int, p: ProjectModel):
    conn = get_conn()
    conn.execute("""UPDATE projects SET code=?,name=?,customer_id=?,manager=?,start_date=?,end_date=?,
        contract_amount=?,status=?,remark=?,updated_at=datetime('now','localtime') WHERE id=?""",
        (p.code,p.name,p.customer_id,p.manager,p.start_date,p.end_date,p.contract_amount,p.status,p.remark,pid))
    conn.commit()
    conn.close()
    return {"ok": True}

@router.delete("/projects/{pid}")
def delete_project(pid: int):
    # 保护：已产生实际业务数据（已入库/已出库/已领用/已开票）的项目禁止删除，避免库存账与单据脱节
    conn = get_conn()
    eff_po = conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE project_id=? AND status>=2",(pid,)).fetchone()[0]
    eff_so = conn.execute("SELECT COUNT(*) FROM sale_orders WHERE project_id=? AND status>=2",(pid,)).fetchone()[0]
    eff_rq = conn.execute("SELECT COUNT(*) FROM requisitions WHERE project_id=? AND status>=2",(pid,)).fetchone()[0]
    has_iv = conn.execute("SELECT COUNT(*) FROM invoices WHERE project_id=?",(pid,)).fetchone()[0]
    if eff_po or eff_so or eff_rq or has_iv:
        conn.close()
        raise HTTPException(400, "该项目已存在实际业务数据（已入库/已出库/已领用/已开票），为保证账实一致，禁止直接删除。请先作废或处理相关单据后，再删除。")
    # 级联清理（草稿单据 + 任务 + 项目本体）
    conn.execute("DELETE FROM project_tasks WHERE project_id=?",(pid,))
    conn.execute("DELETE FROM purchase_items WHERE order_id IN (SELECT id FROM purchase_orders WHERE project_id=?)",(pid,))
    conn.execute("DELETE FROM purchase_orders WHERE project_id=?",(pid,))
    conn.execute("DELETE FROM sale_items WHERE order_id IN (SELECT id FROM sale_orders WHERE project_id=?)",(pid,))
    conn.execute("DELETE FROM sale_orders WHERE project_id=?",(pid,))
    conn.execute("DELETE FROM requisition_items WHERE order_id IN (SELECT id FROM requisitions WHERE project_id=?)",(pid,))
    conn.execute("DELETE FROM requisitions WHERE project_id=?",(pid,))
    conn.execute("DELETE FROM invoices WHERE project_id=?",(pid,))
    conn.execute("DELETE FROM projects WHERE id=?",(pid,))
    conn.commit()
    conn.close()
    return {"ok": True}

# ══════════════════════════════════════════
# 项目任务 / 阶段（项目流程 + 甘特图）
# ══════════════════════════════════════════
class ProjectTaskModel(BaseModel):
    name: str
    assignee: Optional[str] = ""
    start_date: Optional[str] = ""
    end_date: Optional[str] = ""
    progress: Optional[float] = 0
    status: Optional[int] = 1      # 1未开始 2进行中 3已完成 4延期
    parent_id: Optional[int] = 0
    sort_order: Optional[int] = 0
    remark: Optional[str] = ""

@router.get("/projects/{pid}/tasks")
def list_project_tasks(pid: int):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM project_tasks WHERE project_id=? ORDER BY parent_id, sort_order, id",
        (pid,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@router.post("/projects/{pid}/tasks")
def create_project_task(pid: int, t: ProjectTaskModel):
    conn = get_conn()
    cur = conn.execute("""INSERT INTO project_tasks
        (project_id,name,assignee,start_date,end_date,progress,status,parent_id,sort_order,remark)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (pid, t.name, t.assignee, t.start_date, t.end_date, t.progress, t.status,
         t.parent_id, t.sort_order, t.remark))
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return {"ok": True, "id": new_id}

@router.put("/projects/{pid}/tasks/{tid}")
def update_project_task(pid: int, tid: int, t: ProjectTaskModel):
    conn = get_conn()
    conn.execute("""UPDATE project_tasks SET name=?,assignee=?,start_date=?,end_date=?,
        progress=?,status=?,parent_id=?,sort_order=?,remark=?,updated_at=datetime('now','localtime')
        WHERE id=? AND project_id=?""",
        (t.name, t.assignee, t.start_date, t.end_date, t.progress, t.status,
         t.parent_id, t.sort_order, t.remark, tid, pid))
    conn.commit()
    conn.close()
    return {"ok": True}

@router.delete("/projects/{pid}/tasks/{tid}")
def delete_project_task(pid: int, tid: int):
    conn = get_conn()
    conn.execute("DELETE FROM project_tasks WHERE id=? AND project_id=?", (tid, pid))
    conn.commit()
    conn.close()
    return {"ok": True}

# ══════════════════════════════════════════
# 分类（三级）
# ══════════════════════════════════════════
@router.get("/categories")
def list_categories():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM categories ORDER BY level,sort_order,id").fetchall()
    conn.close()
    cats = [dict(r) for r in rows]
    # 构建树形结构
    def build_tree(items, parent_id=0):
        result = []
        for it in items:
            pid = it.get('parent_id', 0) or 0
            if pid == parent_id:
                it['children'] = build_tree(items, it['id'])
                result.append(it)
        return result
    return {"list": cats, "tree": build_tree(cats)}

@router.post("/categories")
def create_category(data: dict):
    conn = get_conn()
    parent_id = int(data.get('parent_id', 0) or 0)
    # 计算层级
    if parent_id == 0:
        level = 1
    else:
        parent = conn.execute("SELECT level FROM categories WHERE id=?", (parent_id,)).fetchone()
        level = (parent['level'] + 1) if parent else 2
        if level > 3:
            conn.close()
            raise HTTPException(400, "最多支持三级分类")
    row = conn.execute("INSERT INTO categories(name,parent_id,level,sort_order) VALUES(?,?,?,?)",
        (data.get('name',''), parent_id, level, data.get('sort_order', 0)))
    new_id = row.lastrowid
    conn.commit()
    conn.close()
    return {"ok": True, "id": new_id}

@router.put("/categories/{cid}")
def update_category(cid: int, data: dict):
    conn = get_conn()
    conn.execute("UPDATE categories SET name=?,sort_order=? WHERE id=?",
        (data.get('name',''), data.get('sort_order',0), cid))
    conn.commit()
    conn.close()
    return {"ok": True}

@router.delete("/categories/{cid}")
def delete_category(cid: int):
    conn = get_conn()
    # 检查是否有子分类或商品
    children = conn.execute("SELECT count(*) FROM categories WHERE parent_id=?", (cid,)).fetchone()[0]
    goods_count = conn.execute("SELECT count(*) FROM goods WHERE category_id=?", (cid,)).fetchone()[0]
    if children > 0:
        conn.close()
        raise HTTPException(400, "请先删除子分类")
    if goods_count > 0:
        conn.close()
        raise HTTPException(400, f"该分类下有 {goods_count} 个商品，无法删除")
    conn.execute("DELETE FROM categories WHERE id=?", (cid,))
    conn.commit()
    conn.close()
    return {"ok": True}

# ══════════════════════════════════════════
# 品牌
# ══════════════════════════════════════════
@router.get("/brands")
def list_brands():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM brands ORDER BY sort_order,id").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@router.post("/brands")
def create_brand(data: dict):
    conn = get_conn()
    try:
        row = conn.execute("INSERT INTO brands(name,remark,sort_order) VALUES(?,?,?)",
            (data.get('name',''), data.get('remark',''), data.get('sort_order',0)))
        new_id = row.lastrowid
        conn.commit()
        return {"ok": True, "id": new_id}
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()

@router.put("/brands/{bid}")
def update_brand(bid: int, data: dict):
    conn = get_conn()
    conn.execute("UPDATE brands SET name=?,remark=?,sort_order=?,status=? WHERE id=?",
        (data.get('name',''), data.get('remark',''), data.get('sort_order',0),
         data.get('status',1), bid))
    conn.commit()
    conn.close()
    return {"ok": True}

@router.delete("/brands/{bid}")
def delete_brand(bid: int):
    conn = get_conn()
    cnt = conn.execute("SELECT count(*) FROM goods WHERE brand_id=?", (bid,)).fetchone()[0]
    if cnt > 0:
        conn.close()
        raise HTTPException(400, f"该品牌下有 {cnt} 个商品，无法删除")
    conn.execute("DELETE FROM brands WHERE id=?", (bid,))
    conn.commit()
    conn.close()
    return {"ok": True}
