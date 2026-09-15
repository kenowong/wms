# -*- coding: utf-8 -*-
"""API路由 - 期初建账

手动录入开账起点（库存 / 应收应付欠款 / 银行账户 / 现金备用金 / 固定资产），
并「应用期初」将期初数据写入业务表，作为后续核算起点：
  - 库存期初  -> inventory 表（覆盖式 upsert）+ 记一条 biz_type='opening' 的库存流水
  - 银行账户期初 -> bank_accounts.balance（SET 覆盖）
  - 现金/备用金期初 -> 生成 acct_type=2 的现金账户，纳入账户体系
  - 应收/应付期初 -> 余额计算时叠加（见 routers_finance 的 ar/ap 计算）
  - 固定资产期初 -> 独立展示，不参与余额（如需折旧后续扩展）

应用一次性（applied=1 后禁止再改，需重置；重置仅允许在未发生任何业务单据时）。
"""
from fastapi import APIRouter, HTTPException, Depends
from database import get_conn
from routers_auth import get_current_user

router = APIRouter()


# ──────────────────────────────────────────────
# 内部工具
# ──────────────────────────────────────────────
def _applied(conn) -> bool:
    r = conn.execute("SELECT applied FROM opening_meta WHERE id=1").fetchone()
    return bool(r and r['applied'])


def _gen_cash_code(conn) -> str:
    """生成现金账户编码  XJ-NNNN"""
    row = conn.execute(
        "SELECT code FROM bank_accounts WHERE code LIKE 'XJ-%' ORDER BY code DESC LIMIT 1"
    ).fetchone()
    seq = 1
    if row:
        try:
            seq = int(row[0].split('-')[-1]) + 1
        except Exception:
            pass
    return f"XJ-{seq:04d}"


# ──────────────────────────────────────────────
# 查询全部期初数据
# ──────────────────────────────────────────────
@router.get("/opening")
def get_opening(user: dict = Depends(get_current_user)):
    conn = get_conn()
    inv = conn.execute(
        """SELECT o.*, g.code AS goods_code, g.name AS goods_name, g.spec, g.model, g.unit,
                  w.name AS warehouse_name, p.name AS project_name
           FROM opening_inventory o
           LEFT JOIN goods g ON o.goods_id=g.id
           LEFT JOIN warehouses w ON o.warehouse_id=w.id
           LEFT JOIN projects p ON o.project_id=p.id
           ORDER BY g.name"""
    ).fetchall()
    debt = conn.execute(
        "SELECT * FROM opening_debt ORDER BY direction, partner_name"
    ).fetchall()
    bank = conn.execute(
        """SELECT o.*, b.name AS account_name, b.acct_type AS account_type
           FROM opening_bank o LEFT JOIN bank_accounts b ON o.bank_account_id=b.id
           ORDER BY o.id"""
    ).fetchall()
    cash = conn.execute("SELECT * FROM opening_cash ORDER BY id").fetchall()
    asset = conn.execute("SELECT * FROM opening_asset ORDER BY id").fetchall()
    meta = conn.execute("SELECT * FROM opening_meta WHERE id=1").fetchone()
    conn.close()
    return {
        "applied": bool(meta and meta['applied']),
        "applied_at": meta['applied_at'] if meta else '',
        "applied_by": meta['applied_by'] if meta else '',
        "version": meta['version'] if meta else 0,
        "inventory": [dict(r) for r in inv],
        "debt": [dict(r) for r in debt],
        "bank": [dict(r) for r in bank],
        "cash": [dict(r) for r in cash],
        "asset": [dict(r) for r in asset],
    }


# ──────────────────────────────────────────────
# 保存：期初库存（整表替换）
# ──────────────────────────────────────────────
@router.post("/opening/inventory")
def save_inventory(body: dict, user: dict = Depends(get_current_user)):
    if _applied(get_conn()):
        raise HTTPException(400, "期初已应用，如需修改请先「重置」（仅限未发生任何业务单据时）")
    items = body.get('items', []) or []
    conn = get_conn()
    try:
        conn.execute("DELETE FROM opening_inventory")
        cnt = 0
        for it in items:
            gid = int(it.get('goods_id') or 0)
            wid = int(it.get('warehouse_id') or 0)
            if not gid or not wid:
                continue
            qty = float(it.get('qty') or 0)
            avg_cost = float(it.get('avg_cost') or 0)
            if 'total_cost' in it and it.get('total_cost') not in (None, ''):
                total = float(it.get('total_cost') or 0)
            else:
                total = round(qty * avg_cost, 2)
            conn.execute(
                """INSERT INTO opening_inventory(goods_id,warehouse_id,project_id,qty,avg_cost,total_cost,remark)
                   VALUES(?,?,?,?,?,?,?)""",
                (gid, wid, int(it.get('project_id') or 0), qty, avg_cost, total,
                 (it.get('remark') or '')))
            cnt += 1
        conn.commit()
        return {"ok": True, "count": cnt}
    except Exception as e:
        conn.rollback()
        raise HTTPException(400, str(e))
    finally:
        conn.close()


# ──────────────────────────────────────────────
# 保存：期初欠款（应收/应付，整表替换）
# ──────────────────────────────────────────────
@router.post("/opening/debt")
def save_debt(body: dict, user: dict = Depends(get_current_user)):
    if _applied(get_conn()):
        raise HTTPException(400, "期初已应用，如需修改请先「重置」（仅限未发生任何业务单据时）")
    items = body.get('items', []) or []
    conn = get_conn()
    try:
        conn.execute("DELETE FROM opening_debt")
        cnt = 0
        for it in items:
            pid = int(it.get('partner_id') or 0)
            if not pid:
                continue
            direction = int(it.get('direction') or 1)
            balance = float(it.get('balance') or 0)
            name = conn.execute("SELECT name FROM partners WHERE id=?", (pid,)).fetchone()
            pname = name['name'] if name else ''
            conn.execute(
                """INSERT INTO opening_debt(partner_id,partner_name,direction,balance,remark)
                   VALUES(?,?,?,?,?)""",
                (pid, pname, direction, balance, (it.get('remark') or '')))
            cnt += 1
        conn.commit()
        return {"ok": True, "count": cnt}
    except Exception as e:
        conn.rollback()
        raise HTTPException(400, str(e))
    finally:
        conn.close()


# ──────────────────────────────────────────────
# 保存：期初银行账户余额（整表替换）
# ──────────────────────────────────────────────
@router.post("/opening/bank")
def save_bank(body: dict, user: dict = Depends(get_current_user)):
    if _applied(get_conn()):
        raise HTTPException(400, "期初已应用，如需修改请先「重置」（仅限未发生任何业务单据时）")
    items = body.get('items', []) or []
    conn = get_conn()
    try:
        conn.execute("DELETE FROM opening_bank")
        cnt = 0
        for it in items:
            aid = int(it.get('bank_account_id') or 0)
            if not aid:
                continue
            bal = float(it.get('balance') or 0)
            name = conn.execute("SELECT name FROM bank_accounts WHERE id=?", (aid,)).fetchone()
            aname = name['name'] if name else ''
            conn.execute(
                "INSERT INTO opening_bank(bank_account_id,account_name,balance) VALUES(?,?,?)",
                (aid, aname, bal))
            cnt += 1
        conn.commit()
        return {"ok": True, "count": cnt}
    except Exception as e:
        conn.rollback()
        raise HTTPException(400, str(e))
    finally:
        conn.close()


# ──────────────────────────────────────────────
# 保存：期初现金/备用金（整表替换）
# ──────────────────────────────────────────────
@router.post("/opening/cash")
def save_cash(body: dict, user: dict = Depends(get_current_user)):
    if _applied(get_conn()):
        raise HTTPException(400, "期初已应用，如需修改请先「重置」（仅限未发生任何业务单据时）")
    items = body.get('items', []) or []
    conn = get_conn()
    try:
        conn.execute("DELETE FROM opening_cash")
        cnt = 0
        for it in items:
            holder = (it.get('holder') or '').strip()
            if not holder:
                continue
            bal = float(it.get('balance') or 0)
            conn.execute(
                "INSERT INTO opening_cash(holder,balance,remark) VALUES(?,?,?)",
                (holder, bal, (it.get('remark') or '')))
            cnt += 1
        conn.commit()
        return {"ok": True, "count": cnt}
    except Exception as e:
        conn.rollback()
        raise HTTPException(400, str(e))
    finally:
        conn.close()


# ──────────────────────────────────────────────
# 保存：期初固定资产（整表替换）
# ──────────────────────────────────────────────
@router.post("/opening/asset")
def save_asset(body: dict, user: dict = Depends(get_current_user)):
    if _applied(get_conn()):
        raise HTTPException(400, "期初已应用，如需修改请先「重置」（仅限未发生任何业务单据时）")
    items = body.get('items', []) or []
    conn = get_conn()
    try:
        conn.execute("DELETE FROM opening_asset")
        cnt = 0
        for it in items:
            name = (it.get('name') or '').strip()
            if not name:
                continue
            conn.execute(
                """INSERT INTO opening_asset(name,category,spec,original_value,accum_depreciation,remark)
                   VALUES(?,?,?,?,?,?)""",
                (name, (it.get('category') or ''), (it.get('spec') or ''),
                 float(it.get('original_value') or 0), float(it.get('accum_depreciation') or 0),
                 (it.get('remark') or '')))
            cnt += 1
        conn.commit()
        return {"ok": True, "count": cnt}
    except Exception as e:
        conn.rollback()
        raise HTTPException(400, str(e))
    finally:
        conn.close()


# ──────────────────────────────────────────────
# 应用期初（写入业务表，作为核算起点）
# ──────────────────────────────────────────────
@router.post("/opening/apply")
def apply_opening(user: dict = Depends(get_current_user)):
    conn = get_conn()
    try:
        # ── 库存期初 ──
        inv = conn.execute("SELECT * FROM opening_inventory").fetchall()
        if not inv:
            raise HTTPException(400, "请先录入期初库存再应用")
        # 清掉上一次应用的期初流水（幂等）
        conn.execute("DELETE FROM inventory_logs WHERE biz_type='opening'")
        for r in inv:
            gid, wid, pid = r['goods_id'], r['warehouse_id'], r['project_id'] or 0
            qty, avg_cost, total = r['qty'], r['avg_cost'], r['total_cost']
            ex = conn.execute(
                "SELECT qty FROM inventory WHERE goods_id=? AND warehouse_id=? AND project_id=?",
                (gid, wid, pid)).fetchone()
            before = ex['qty'] if ex else 0
            if ex:
                conn.execute(
                    "UPDATE inventory SET qty=?, avg_cost=?, total_cost=? WHERE goods_id=? AND warehouse_id=? AND project_id=?",
                    (qty, avg_cost, total, gid, wid, pid))
            else:
                conn.execute(
                    """INSERT INTO inventory(goods_id,warehouse_id,project_id,qty,avg_cost,total_cost,updated_at)
                       VALUES(?,?,?,?,?,?,datetime('now','localtime'))""",
                    (gid, wid, pid, qty, avg_cost, total))
            gname = conn.execute("SELECT name FROM goods WHERE id=?", (gid,)).fetchone()
            gname = gname['name'] if gname else ''
            wname = conn.execute("SELECT name FROM warehouses WHERE id=?", (wid,)).fetchone()
            wname = wname['name'] if wname else ''
            pname = conn.execute("SELECT name FROM projects WHERE id=?", (pid,)).fetchone()
            pname = pname['name'] if pname else ''
            conn.execute(
                """INSERT INTO inventory_logs(goods_id,goods_name,warehouse_id,warehouse_name,
                   project_id,project_name,biz_type,biz_order_no,direction,qty,before_qty,after_qty,
                   cost_price,operator,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now','localtime'))""",
                (gid, gname, wid, wname, pid, pname, 'opening', '期初建账', 1, qty,
                 before, qty, avg_cost, user['display_name']))

        # ── 银行账户期初（SET 覆盖，幂等）──
        for r in conn.execute("SELECT * FROM opening_bank").fetchall():
            conn.execute("UPDATE bank_accounts SET balance=? WHERE id=?",
                         (r['balance'], r['bank_account_id']))

        # ── 现金/备用金期初（重建 acct_type=2 的现金账户）──
        conn.execute("DELETE FROM bank_accounts WHERE acct_type=2")
        for r in conn.execute("SELECT * FROM opening_cash").fetchall():
            conn.execute(
                """INSERT INTO bank_accounts(code,name,bank_name,account_no,currency,
                   opening_balance,balance,status,remark,acct_type)
                   VALUES(?,?,?,?,?,?,?,?,?,2)""",
                (_gen_cash_code(conn), r['holder'] or '现金', '', '', 'CNY',
                 r['balance'], r['balance'], 1, r['remark'] or ''))

        # ── 应收/应付期初：标记 applied，余额计算自动叠加 ──
        # ── 固定资产期初：仅展示，无需落库 ──
        meta = conn.execute("SELECT * FROM opening_meta WHERE id=1").fetchone()
        if meta:
            conn.execute(
                "UPDATE opening_meta SET applied=1, applied_at=datetime('now','localtime'), "
                "applied_by=?, version=version+1 WHERE id=1",
                (user['display_name'],))
        else:
            conn.execute(
                "INSERT INTO opening_meta(id,applied,applied_at,applied_by,version) "
                "VALUES(1,1,datetime('now','localtime'),?,1)",
                (user['display_name'],))
        conn.commit()
        return {"ok": True,
                "msg": "期初已应用：库存已写入台账、银行账户/现金余额已设定、应收应付期初已纳入余额计算"}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(400, str(e))
    finally:
        conn.close()


# ──────────────────────────────────────────────
# 重置期初（仅未发生任何业务单据时允许）
# ──────────────────────────────────────────────
@router.post("/opening/reset")
def reset_opening(user: dict = Depends(get_current_user)):
    conn = get_conn()
    try:
        biz = conn.execute(
            """SELECT (SELECT count(*) FROM purchase_orders)
                    + (SELECT count(*) FROM sale_orders)
                    + (SELECT count(*) FROM payments)
                    + (SELECT count(*) FROM requisitions)
                    + (SELECT count(*) FROM expenses)"""
        ).fetchone()
        if biz and biz[0] and biz[0] > 0:
            conn.close()
            raise HTTPException(
                400, "已存在业务单据，无法重置期初。库存请通过盘点调整，余额请通过对账/收付款处理；"
                     "若确需清空，请作废并删除全部业务单据后再重置。")
        # 仅期初行，清空库存
        conn.execute("DELETE FROM inventory_logs WHERE biz_type='opening'")
        conn.execute("DELETE FROM inventory")
        # 现金期初账户删除
        conn.execute("DELETE FROM bank_accounts WHERE acct_type=2")
        # 银行账户余额无法还原到应用前，提示用户手动调整
        conn.execute("UPDATE opening_meta SET applied=0, applied_at='', applied_by='' WHERE id=1")
        conn.commit()
        return {"ok": True,
                "warn": "已解除期初锁定，可重新录入。注意：银行账户余额保持当前值（期初 SET 写入），如需还原请手动调整。"}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(400, str(e))
    finally:
        conn.close()
