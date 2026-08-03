# -*- coding: utf-8 -*-
"""API路由 - 期末处理：年底封账与结转

封账（close）  ：锁定某会计年度，禁止再向该年写入任何业务单据。
结转（carry-forward）：将封账年度的期末余额生成为下一年度的期初余额
                      （库存期初 / 往来应收应付期初 / 银行账户期初），
                      便于跨年度连续核算与按年出具报表。
反封账（reopen）：解除封账，并清除该年结转所产生的下年期初数据。
"""
import datetime
from fastapi import APIRouter, HTTPException, Depends
from database import get_conn
from routers_auth import get_current_user

router = APIRouter()


# ──────────────────────────────────────────────
# 内部工具
# ──────────────────────────────────────────────
def _min_doc_year(conn):
    """取所有业务单据中最早的年份；无单据则返回当前年"""
    min_y = None
    for tbl, col in [
        ('purchase_orders', 'order_date'),
        ('sale_orders', 'order_date'),
        ('requisitions', 'req_date'),
        ('payments', 'pay_date'),
        ('expenses', 'expense_date'),
        ('invoices', 'invoice_date'),
        ('inventory_checks', 'check_date'),
    ]:
        try:
            r = conn.execute(
                f"SELECT MIN(CAST(substr({col},1,4) AS INTEGER)) FROM {tbl} "
                f"WHERE {col} IS NOT NULL AND {col} <> ''"
            ).fetchone()
            if r and r[0]:
                y = int(r[0])
                min_y = y if min_y is None else min(min_y, y)
        except Exception:
            pass
    return min_y if min_y else datetime.date.today().year


def _year_end(year):
    """该年年末（含时间，用于 <= 比较）"""
    return f"{year}-12-31 23:59:59"


# ──────────────────────────────────────────────
# 年份状态列表
# ──────────────────────────────────────────────
@router.get("/period/years")
def period_years(user: dict = Depends(get_current_user)):
    conn = get_conn()
    cur = datetime.date.today().year
    min_y = _min_doc_year(conn)
    rows = conn.execute("SELECT * FROM year_closings").fetchall()
    status_map = {r['fiscal_year']: dict(r) for r in rows}
    # 列表下界：最早单据年份、当前年、已封账年份三者取最小，确保空单据的封账年份也能显示
    closed_years = [r['fiscal_year'] for r in rows]
    start = min([min_y, cur] + closed_years) if closed_years else min(min_y, cur)

    years = []
    for y in range(start, cur + 1):
        c = status_map.get(y)
        years.append({
            "year": y,
            "status": c['status'] if c else 0,
            "closed_at": c['closed_at'] if c else '',
            "closed_by": c['closed_by'] if c else '',
            "carry_forwarded": c['carry_forwarded'] if c else 0,
            "carried_at": c['carried_at'] if c else '',
            "carried_by": c['carried_by'] if c else '',
            "remark": c['remark'] if c else '',
        })
    conn.close()
    return {"current_year": cur, "min_year": min_y, "years": years}


# ──────────────────────────────────────────────
# 封账
# ──────────────────────────────────────────────
@router.post("/period/close")
def period_close(body: dict, user: dict = Depends(get_current_user)):
    try:
        year = int(body.get('year'))
    except Exception:
        raise HTTPException(400, "年份参数错误")
    cur = datetime.date.today().year
    if year > cur:
        raise HTTPException(400, f"不能封账未来年份（{year} 年）")
    if year < 2000:
        raise HTTPException(400, "年份不合法")

    conn = get_conn()
    ex = conn.execute(
        "SELECT status FROM year_closings WHERE fiscal_year=?", (year,)
    ).fetchone()
    if ex and ex['status'] >= 1:
        conn.close()
        raise HTTPException(400, f"{year} 年已封账，无需重复操作")
    # 提示：若有更早的未封账年度，建议按顺序封账（非强制）
    earlier = conn.execute(
        "SELECT fiscal_year FROM year_closings WHERE fiscal_year<? AND status=0",
        (year,)
    ).fetchall()
    conn.execute(
        """INSERT INTO year_closings(fiscal_year,status,closed_at,closed_by,carry_forwarded,remark)
           VALUES(?,1,datetime('now','localtime'),?,0,?)""",
        (year, user['display_name'], (body.get('remark') or '').strip())
    )
    conn.commit()
    conn.close()
    return {"ok": True, "year": year, "status": 1,
            "warn_earlier_open": [r[0] for r in earlier]}


# ──────────────────────────────────────────────
# 反封账
# ──────────────────────────────────────────────
@router.post("/period/reopen")
def period_reopen(body: dict, user: dict = Depends(get_current_user)):
    try:
        year = int(body.get('year'))
    except Exception:
        raise HTTPException(400, "年份参数错误")
    conn = get_conn()
    ex = conn.execute(
        "SELECT status FROM year_closings WHERE fiscal_year=?", (year,)
    ).fetchone()
    if not ex or ex['status'] == 0:
        conn.close()
        raise HTTPException(400, f"{year} 年未封账，无需反封账")

    # 清除该年结转所产生的"下年期初"（fiscal_year = year+1）
    conn.execute("DELETE FROM inventory_opening WHERE fiscal_year=?", (year + 1,))
    conn.execute("DELETE FROM partner_opening WHERE fiscal_year=?", (year + 1,))
    conn.execute("DELETE FROM bank_opening WHERE fiscal_year=?", (year + 1,))
    conn.execute("DELETE FROM year_closings WHERE fiscal_year=?", (year,))
    conn.commit()
    conn.close()
    return {"ok": True, "year": year}


# ──────────────────────────────────────────────
# 结转（生成下年期初余额）
# ──────────────────────────────────────────────
@router.post("/period/carry-forward")
def period_carry_forward(body: dict, user: dict = Depends(get_current_user)):
    try:
        year = int(body.get('year'))
    except Exception:
        raise HTTPException(400, "年份参数错误")
    conn = get_conn()
    ex = conn.execute(
        "SELECT status FROM year_closings WHERE fiscal_year=?", (year,)
    ).fetchone()
    if not ex or ex['status'] < 1:
        conn.close()
        raise HTTPException(400, f"请先封账 {year} 年，再执行结转")
    ny = year + 1
    ye = _year_end(year)
    try:
        # 幂等：先清掉该年已生成的期初
        conn.execute("DELETE FROM inventory_opening WHERE fiscal_year=?", (ny,))
        conn.execute("DELETE FROM partner_opening WHERE fiscal_year=?", (ny,))
        conn.execute("DELETE FROM bank_opening WHERE fiscal_year=?", (ny,))

        # ── 库存期初（当前实时库存快照）──
        inv = conn.execute(
            "SELECT goods_id,warehouse_id,project_id,qty,avg_cost,total_cost "
            "FROM inventory"
        ).fetchall()
        inv_cnt = 0
        for r in inv:
            if (r['qty'] or 0) == 0 and (r['total_cost'] or 0) == 0:
                continue
            conn.execute(
                """INSERT INTO inventory_opening
                   (fiscal_year,goods_id,warehouse_id,project_id,qty,avg_cost,total_cost)
                   VALUES(?,?,?,?,?,?,?)""",
                (ny, r['goods_id'], r['warehouse_id'], r['project_id'] or 0,
                 r['qty'], r['avg_cost'], r['total_cost']))
            inv_cnt += 1

        # ── 往来期初：应收（客户）──
        cust = conn.execute(
            """SELECT p.id, p.name,
                  COALESCE((SELECT SUM(total_amount) FROM sale_orders
                             WHERE customer_id=p.id AND status>=2 AND order_date<=?),0) AS sales,
                  COALESCE((SELECT SUM(amount) FROM payments
                             WHERE partner_id=p.id AND pay_type=1 AND status=1 AND pay_date<=?),0) AS recv
               FROM partners p WHERE p.type IN (2,3)""",
            (ye, ye)
        ).fetchall()
        part_cnt = 0
        for r in cust:
            bal = round((r['sales'] or 0) - (r['recv'] or 0), 2)
            if abs(bal) < 0.001:
                continue
            conn.execute(
                "INSERT INTO partner_opening(fiscal_year,partner_id,partner_name,direction,balance) "
                "VALUES(?,?,?,1,?)",
                (ny, r['id'], r['name'], bal))
            part_cnt += 1

        # ── 往来期初：应付（供应商）──
        supp = conn.execute(
            """SELECT p.id, p.name,
                  COALESCE((SELECT SUM(total_amount) FROM purchase_orders
                             WHERE supplier_id=p.id AND status>=2 AND order_date<=?),0) AS purch,
                  COALESCE((SELECT SUM(amount) FROM payments
                             WHERE partner_id=p.id AND pay_type=2 AND status=1 AND pay_date<=?),0) AS paid
               FROM partners p WHERE p.type IN (1,3)""",
            (ye, ye)
        ).fetchall()
        for r in supp:
            bal = round((r['purch'] or 0) - (r['paid'] or 0), 2)
            if abs(bal) < 0.001:
                continue
            conn.execute(
                "INSERT INTO partner_opening(fiscal_year,partner_id,partner_name,direction,balance) "
                "VALUES(?,?,?,2,?)",
                (ny, r['id'], r['name'], bal))
            part_cnt += 1

        # ── 银行账户期初（当前余额快照）──
        banks = conn.execute(
            "SELECT id,name,balance FROM bank_accounts WHERE status=1"
        ).fetchall()
        bank_cnt = 0
        for r in banks:
            if abs(r['balance'] or 0) < 0.001:
                continue
            conn.execute(
                "INSERT INTO bank_opening(fiscal_year,bank_account_id,account_name,balance) "
                "VALUES(?,?,?,?)",
                (ny, r['id'], r['name'], r['balance']))
            bank_cnt += 1

        conn.execute(
            """UPDATE year_closings SET status=2, carry_forwarded=1,
                   carried_at=datetime('now','localtime'), carried_by=?
               WHERE fiscal_year=?""",
            (user['display_name'], year)
        )
        conn.commit()
        return {
            "ok": True, "year": year, "next_year": ny,
            "inventory_items": inv_cnt,
            "partner_items": part_cnt,
            "bank_items": bank_cnt,
        }
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(400, str(e))
    finally:
        conn.close()


# ──────────────────────────────────────────────
# 查看某年期初余额（结转结果）
# ──────────────────────────────────────────────
@router.get("/period/opening/{year}")
def period_opening(year: int):
    conn = get_conn()
    inv = conn.execute(
        """SELECT o.goods_id, g.code, g.name, g.spec, g.model, g.unit,
                  w.name AS warehouse_name, o.qty, o.avg_cost, o.total_cost
           FROM inventory_opening o
           LEFT JOIN goods g ON o.goods_id=g.id
           LEFT JOIN warehouses w ON o.warehouse_id=w.id
           WHERE o.fiscal_year=? ORDER BY g.name""",
        (year,)
    ).fetchall()
    part = conn.execute(
        """SELECT partner_name, direction, balance FROM partner_opening
           WHERE fiscal_year=? ORDER BY direction, partner_name""",
        (year,)
    ).fetchall()
    bank = conn.execute(
        """SELECT account_name, balance FROM bank_opening
           WHERE fiscal_year=? ORDER BY account_name""",
        (year,)
    ).fetchall()
    conn.close()
    return {
        "year": year,
        "inventory": [dict(r) for r in inv],
        "partners": [dict(r) for r in part],
        "banks": [dict(r) for r in bank],
    }
