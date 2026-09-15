# -*- coding: utf-8 -*-
"""API路由 - 银行账户、收付款、对账"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from database import get_conn
from period_guard import assert_dates_open
from routers_auth import get_current_user
import datetime

router = APIRouter()


# ══════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════
def _gen_pay_no(conn, pay_type: int) -> str:
    """生成收付款单号  SK-YYYYMMDD-NNNN / FK-YYYYMMDD-NNNN"""
    prefix = "SK" if pay_type == 1 else "FK"
    today = datetime.date.today().strftime('%Y%m%d')
    pat = f"{prefix}-{today}-%"
    row = conn.execute(
        "SELECT pay_no FROM payments WHERE pay_no LIKE ? ORDER BY pay_no DESC LIMIT 1",
        (pat,)).fetchone()
    seq = 1
    if row:
        try:
            seq = int(row[0].split('-')[-1]) + 1
        except Exception:
            pass
    return f"{prefix}-{today}-{seq:04d}"


def _gen_recon_no(conn) -> str:
    """生成对账单号  RC-YYYYMMDD-NNNN"""
    today = datetime.date.today().strftime('%Y%m%d')
    pat = f"RC-{today}-%"
    row = conn.execute(
        "SELECT recon_no FROM reconciliations WHERE recon_no LIKE ? ORDER BY recon_no DESC LIMIT 1",
        (pat,)).fetchone()
    seq = 1
    if row:
        try:
            seq = int(row[0].split('-')[-1]) + 1
        except Exception:
            pass
    return f"RC-{today}-{seq:04d}"


def _gen_bank_code(conn) -> str:
    """生成银行账户编码  BA-NNNN"""
    row = conn.execute(
        "SELECT code FROM bank_accounts WHERE code LIKE 'BA-%' ORDER BY code DESC LIMIT 1"
    ).fetchone()
    seq = 1
    if row:
        try:
            seq = int(row[0].split('-')[-1]) + 1
        except Exception:
            pass
    return f"BA-{seq:04d}"


# ══════════════════════════════════════════
# 银行账户
# ══════════════════════════════════════════
@router.get("/bank_accounts")
def list_bank_accounts():
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM bank_accounts ORDER BY sort_order, id"
        if _has_col(conn, 'bank_accounts', 'sort_order') else
        "SELECT * FROM bank_accounts ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _has_col(conn, tbl, col):
    try:
        info = conn.execute(f"PRAGMA table_info({tbl})").fetchall()
        return any(r['name'] == col for r in info)
    except Exception:
        return False


@router.post("/bank_accounts")
def create_bank_account(data: dict):
    conn = get_conn()
    try:
        code = (data.get('code') or '').strip() or _gen_bank_code(conn)
        row = conn.execute(
            """INSERT INTO bank_accounts(code,name,bank_name,account_no,currency,
               opening_balance,balance,status,remark)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (code, data.get('name', ''), data.get('bank_name', ''),
             data.get('account_no', ''), data.get('currency', 'CNY'),
             data.get('opening_balance', 0), data.get('opening_balance', 0),
             data.get('status', 1), data.get('remark', ''))
        )
        conn.commit()
        return {"ok": True, "id": row.lastrowid, "code": code}
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()


@router.put("/bank_accounts/{aid}")
def update_bank_account(aid: int, data: dict):
    conn = get_conn()
    conn.execute(
        """UPDATE bank_accounts SET name=?,bank_name=?,account_no=?,currency=?,
           status=?,remark=? WHERE id=?""",
        (data.get('name', ''), data.get('bank_name', ''), data.get('account_no', ''),
         data.get('currency', 'CNY'), data.get('status', 1), data.get('remark', ''), aid)
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@router.delete("/bank_accounts/{aid}")
def delete_bank_account(aid: int):
    conn = get_conn()
    cnt = conn.execute(
        "SELECT count(*) FROM payments WHERE bank_account_id=?", (aid,)
    ).fetchone()[0]
    if cnt > 0:
        conn.close()
        raise HTTPException(400, f"该账户已有 {cnt} 笔收付款记录，无法删除")
    conn.execute("DELETE FROM bank_accounts WHERE id=?", (aid,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ══════════════════════════════════════════
# 收付款
# ══════════════════════════════════════════
@router.get("/payments")
def list_payments(
    pay_type: int = -1,
    partner_id: int = -1,
    bank_account_id: int = -1,
    date_from: str = "",
    date_to: str = "",
    keyword: str = ""
):
    conn = get_conn()
    sql = """SELECT p.*,
        pt.name as partner_name_ref,
        ba.name as bank_account_name,
        ba.account_no as bank_account_no
        FROM payments p
        LEFT JOIN partners pt ON p.partner_id=pt.id
        LEFT JOIN bank_accounts ba ON p.bank_account_id=ba.id
        WHERE p.status=1"""
    params = []
    if pay_type >= 0:
        sql += " AND p.pay_type=?"; params.append(pay_type)
    if partner_id >= 0:
        sql += " AND p.partner_id=?"; params.append(partner_id)
    if bank_account_id >= 0:
        sql += " AND p.bank_account_id=?"; params.append(bank_account_id)
    if date_from:
        sql += " AND p.pay_date>=?"; params.append(date_from)
    if date_to:
        sql += " AND p.pay_date<=?"; params.append(date_to)
    if keyword:
        sql += " AND (p.pay_no LIKE ? OR p.partner_name LIKE ? OR p.remark LIKE ?)"
        params += [f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"]
    sql += " ORDER BY p.pay_date DESC, p.id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/payments/{pid}")
def get_payment(pid: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "记录不存在")
    pay = dict(row)
    # 关联单据
    rels = conn.execute(
        "SELECT * FROM payment_relations WHERE payment_id=?", (pid,)
    ).fetchall()
    pay['relations'] = [dict(r) for r in rels]
    conn.close()
    return pay


@router.get("/finance/pending-orders")
def pending_orders(pay_type: int = 1, partner_id: int = -1):
    """待收/待付订单：已审核(status>=2)且仍有未核销余额的出入库单。
    pay_type=1 收款 -> 销售单(按客户)；pay_type=2 付款 -> 采购单(按供应商)"""
    conn = get_conn()
    try:
        if pay_type == 1:
            otype = 'sale'
            sql = """SELECT o.id, o.order_no, o.order_date, o.customer_id AS pid,
                        o.total_amount,
                        COALESCE((SELECT SUM(pr.write_off_amount) FROM payment_relations pr
                            JOIN payments p ON pr.payment_id=p.id
                            WHERE pr.order_type='sale' AND pr.order_id=o.id
                              AND p.status=1 AND p.audit_status!=2),0) AS paid
                     FROM sale_orders o WHERE o.status>=2"""
            params = []
            if partner_id and partner_id >= 0:
                sql += " AND o.customer_id=?"
                params = [partner_id]
        else:
            otype = 'purchase'
            sql = """SELECT o.id, o.order_no, o.order_date, o.supplier_id AS pid,
                        o.total_amount,
                        COALESCE((SELECT SUM(pr.write_off_amount) FROM payment_relations pr
                            JOIN payments p ON pr.payment_id=p.id
                            WHERE pr.order_type='purchase' AND pr.order_id=o.id
                              AND p.status=1 AND p.audit_status!=2),0) AS paid
                     FROM purchase_orders o WHERE o.status>=2"""
            params = []
            if partner_id and partner_id >= 0:
                sql += " AND o.supplier_id=?"
                params = [partner_id]
        sql += " ORDER BY o.order_date DESC, o.id DESC"
        rows = conn.execute(sql, params).fetchall()
        results = []
        for r in rows:
            total = r['total_amount'] or 0
            paid = r['paid'] or 0
            remaining = round(total - paid, 2)
            if remaining <= 0.001:
                continue
            pname = ''
            if r['pid']:
                pn = conn.execute("SELECT name FROM partners WHERE id=?", (r['pid'],)).fetchone()
                if pn:
                    pname = pn['name']
            results.append({
                'order_type': otype,
                'order_id': r['id'],
                'order_no': r['order_no'],
                'order_date': r['order_date'],
                'partner_id': r['pid'],
                'partner_name': pname,
                'total_amount': total,
                'paid_amount': round(paid, 2),
                'remaining': remaining,
            })
        # 期初欠款（仅应用期初后、且仍有未核销余额）：direction 1 应收→收款，2 应付→付款
        _meta = conn.execute("SELECT applied FROM opening_meta WHERE id=1").fetchone()
        if _meta and _meta[0]:
            od_rows = conn.execute(
                "SELECT partner_id, partner_name, direction, COALESCE(SUM(balance),0) AS bal "
                "FROM opening_debt GROUP BY partner_id, direction"
            ).fetchall()
            for od in od_rows:
                bal = od['bal'] or 0
                if bal <= 0:
                    continue
                if (pay_type == 1 and od['direction'] != 1) or (pay_type == 2 and od['direction'] != 2):
                    continue
                if partner_id and partner_id >= 0 and od['partner_id'] != partner_id:
                    continue
                wo = conn.execute(
                    """SELECT COALESCE(SUM(pr.write_off_amount),0) FROM payment_relations pr
                       JOIN payments p ON pr.payment_id=p.id
                       WHERE pr.order_type='opening' AND pr.order_id=? AND p.pay_type=?
                         AND p.status=1 AND p.audit_status!=2""",
                    (od['partner_id'], pay_type)).fetchone()[0]
                rem = round(bal - wo, 2)
                if rem <= 0.001:
                    continue
                results.append({
                    'order_type': 'opening',
                    'order_id': od['partner_id'],
                    'order_no': '期初欠款',
                    'order_date': '',
                    'partner_id': od['partner_id'],
                    'partner_name': od['partner_name'],
                    'total_amount': bal,
                    'paid_amount': round(wo, 2),
                    'remaining': rem,
                })
        return results
    finally:
        conn.close()


@router.post("/payments")
def create_payment(data: dict):
    conn = get_conn()
    try:
        pay_type = int(data.get('pay_type', 1))
        pay_no = _gen_pay_no(conn, pay_type)
        pay_date = data.get('pay_date', '') or datetime.date.today().isoformat()
        assert_dates_open(conn, pay_date, label="收付款")
        amount = float(data.get('amount', 0))
        fee = float(data.get('fee', 0))
        partner_id = int(data.get('partner_id', 0)) or None
        bank_account_id = int(data.get('bank_account_id', 0)) or None
        project_id = int(data.get('project_id', 0))

        # 获取往来单位名称
        partner_name = data.get('partner_name', '')
        if partner_id and not partner_name:
            pr = conn.execute("SELECT name FROM partners WHERE id=?", (partner_id,)).fetchone()
            if pr:
                partner_name = pr['name']

        row = conn.execute(
            """INSERT INTO payments(pay_no,pay_type,pay_date,partner_id,partner_name,
               bank_account_id,amount,fee,project_id,remark,operator,status)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,1)""",
            (pay_no, pay_type, pay_date, partner_id, partner_name,
             bank_account_id, amount, fee, project_id,
             data.get('remark', ''), data.get('operator', ''))
        )
        pay_id = row.lastrowid

        # 关联单据核销（支持一笔收付款拆分到多张单据 / 一张单据分多次收付款）
        relations = data.get('relations', [])
        sum_wo = 0.0
        for rel in relations:
            wo = float(rel.get('write_off_amount', 0))
            if wo <= 0:
                continue
            otype = rel.get('order_type', '')
            oid = int(rel.get('order_id', 0) or 0)
            if otype not in ('purchase', 'sale', 'opening') or oid <= 0:
                raise HTTPException(400, "关联单据类型或编号无效")
            if otype == 'sale':
                tot = conn.execute("SELECT total_amount FROM sale_orders WHERE id=?", (oid,)).fetchone()
            elif otype == 'purchase':
                tot = conn.execute("SELECT total_amount FROM purchase_orders WHERE id=?", (oid,)).fetchone()
            else:  # opening 期初欠款：oid 存 partner_id，余额取对应方向期初
                od = conn.execute(
                    "SELECT COALESCE(SUM(balance),0) FROM opening_debt WHERE partner_id=? AND direction=?",
                    (oid, 1 if pay_type == 1 else 2)).fetchone()
                tot = {'total_amount': od[0]} if od else None
            if not tot:
                raise HTTPException(400, "关联单据不存在")
            # 已核销金额（仅统计有效付款：status=1 且未作废 audit_status!=2）
            cur_paid = conn.execute(
                """SELECT COALESCE(SUM(pr.write_off_amount),0) FROM payment_relations pr
                   JOIN payments p ON pr.payment_id=p.id
                   WHERE pr.order_type=? AND pr.order_id=? AND p.status=1 AND p.audit_status!=2""",
                (otype, oid)).fetchone()[0]
            remaining = (tot['total_amount'] or 0) - cur_paid
            if wo > remaining + 0.01:
                raise HTTPException(400, f"单据 {rel.get('order_no','')} 本次核销 {wo} 超过剩余未结 {round(remaining,2)}")
            conn.execute(
                """INSERT INTO payment_relations(payment_id,order_type,order_id,order_no,write_off_amount)
                   VALUES(?,?,?,?,?)""",
                (pay_id, otype, oid, rel.get('order_no', ''), wo)
            )
            sum_wo += wo
        if sum_wo > amount + 0.01:
            raise HTTPException(400, f"核销合计 {round(sum_wo,2)} 超过本次收付款金额 {amount}")

        # 更新银行账户余额
        if bank_account_id:
            if pay_type == 1:  # 收款 → 余额增加
                conn.execute(
                    "UPDATE bank_accounts SET balance=balance+? WHERE id=?",
                    (amount - fee, bank_account_id)
                )
            else:  # 付款 → 余额减少
                conn.execute(
                    "UPDATE bank_accounts SET balance=balance-? WHERE id=?",
                    (amount + fee, bank_account_id)
                )

        conn.commit()
        return {"ok": True, "id": pay_id, "pay_no": pay_no}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()


def _payment_guard(conn, pid, allow):
    row = conn.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    if not row:
        raise HTTPException(404, "记录不存在")
    if row['audit_status'] not in allow:
        raise HTTPException(400, "该收付款已审核/已作废，不能修改。请先反审核或作废。")
    return row

@router.post("/payments/{pid}/approve")
def approve_payment(pid: int):
    conn = get_conn(); row = _payment_guard(conn, pid, (0,))
    assert_dates_open(conn, row['pay_date'], label="收付款")
    conn.execute("UPDATE payments SET audit_status=1 WHERE id=?", (pid,))
    conn.commit(); conn.close(); return {"ok": True}

@router.post("/payments/{pid}/unapprove")
def unapprove_payment(pid: int):
    conn = get_conn(); row = _payment_guard(conn, pid, (1,))
    assert_dates_open(conn, row['pay_date'], label="收付款")
    conn.execute("UPDATE payments SET audit_status=0 WHERE id=?", (pid,))
    conn.commit(); conn.close(); return {"ok": True}

@router.post("/payments/{pid}/void")
def void_payment(pid: int):
    """作废：回滚银行余额 + 标记已作废"""
    conn = get_conn(); row = _payment_guard(conn, pid, (1,))
    assert_dates_open(conn, row['pay_date'], label="收付款")
    pay = conn.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    pay = dict(pay)
    if pay.get('bank_account_id'):
        if pay['pay_type'] == 1:
            conn.execute("UPDATE bank_accounts SET balance=balance-? WHERE id=?",
                         (pay['amount'] - pay['fee'], pay['bank_account_id']))
        else:
            conn.execute("UPDATE bank_accounts SET balance=balance+? WHERE id=?",
                         (pay['amount'] + pay['fee'], pay['bank_account_id']))
    conn.execute("UPDATE payments SET audit_status=2 WHERE id=?", (pid,))
    conn.commit(); conn.close(); return {"ok": True}

@router.put("/payments/{pid}")
def update_payment(pid: int, data: dict):
    """仅允许修改备注、操作人、日期等非金额字段"""
    conn = get_conn()
    _payment_guard(conn, pid, (0,))
    assert_dates_open(conn, data.get('pay_date', ''), label="收付款")
    conn.execute(
        "UPDATE payments SET pay_date=?,remark=?,operator=? WHERE id=?",
        (data.get('pay_date', ''), data.get('remark', ''),
         data.get('operator', ''), pid)
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@router.delete("/payments/{pid}")
def cancel_payment(pid: int):
    """撤销收付款（逻辑删除并回滚余额）——仅草稿可用"""
    conn = get_conn()
    pay = conn.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    if not pay:
        conn.close()
        raise HTTPException(404, "记录不存在")
    if pay['audit_status'] >= 1:
        conn.close()
        raise HTTPException(400, "该收付款已审核/已作废，不能撤销。请先反审核。")
    assert_dates_open(conn, pay['pay_date'], label="收付款")
    pay = dict(pay)
    # 回滚银行余额
    if pay.get('bank_account_id'):
        if pay['pay_type'] == 1:
            conn.execute(
                "UPDATE bank_accounts SET balance=balance-? WHERE id=?",
                (pay['amount'] - pay['fee'], pay['bank_account_id'])
            )
        else:
            conn.execute(
                "UPDATE bank_accounts SET balance=balance+? WHERE id=?",
                (pay['amount'] + pay['fee'], pay['bank_account_id'])
            )
    conn.execute("UPDATE payments SET status=0 WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ══════════════════════════════════════════
# 对账
# ══════════════════════════════════════════
@router.get("/reconciliations/{rid}")
def get_reconciliation(rid: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM reconciliations WHERE id=?", (rid,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "对账单不存在")
    return dict(row)


@router.get("/reconciliations")
def list_reconciliations(partner_id: int = -1, recon_type: int = -1):
    conn = get_conn()
    sql = """SELECT r.*, pt.name as partner_name_ref
        FROM reconciliations r
        LEFT JOIN partners pt ON r.partner_id=pt.id
        WHERE 1=1"""
    params = []
    if partner_id >= 0:
        sql += " AND r.partner_id=?"; params.append(partner_id)
    if recon_type >= 0:
        sql += " AND r.recon_type=?"; params.append(recon_type)
    sql += " ORDER BY r.id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/reconciliations/generate")
def generate_reconciliation(data: dict):
    """
    自动生成对账单：
    汇总期间内的采购/销售单金额，以及已收/付款金额，计算差额
    recon_type=1 应收（销售）; recon_type=2 应付（采购）
    """
    conn = get_conn()
    try:
        partner_id = int(data.get('partner_id', 0))
        recon_type = int(data.get('recon_type', 1))
        period_start = data.get('period_start', '')
        period_end = data.get('period_end', '')
        if not partner_id:
            raise HTTPException(400, "请选择往来单位")

        # 查单据金额
        if recon_type == 1:  # 应收 → 销售单
            order_sql = """SELECT COALESCE(SUM(total_amount),0) FROM sale_orders
                WHERE customer_id=? AND status>=2"""
            paid_sql = """SELECT COALESCE(SUM(amount),0) FROM payments
                WHERE partner_id=? AND pay_type=1 AND status=1"""
        else:  # 应付 → 采购单
            order_sql = """SELECT COALESCE(SUM(total_amount),0) FROM purchase_orders
                WHERE supplier_id=? AND status>=2"""
            paid_sql = """SELECT COALESCE(SUM(amount),0) FROM payments
                WHERE partner_id=? AND pay_type=2 AND status=1"""

        if period_start:
            order_sql += " AND order_date>=?"
            paid_sql += " AND pay_date>=?"
        if period_end:
            order_sql += " AND order_date<=?"
            paid_sql += " AND pay_date<=?"

        o_params = [partner_id] + ([period_start] if period_start else []) + ([period_end] if period_end else [])
        p_params = [partner_id] + ([period_start] if period_start else []) + ([period_end] if period_end else [])

        total_order = conn.execute(order_sql, o_params).fetchone()[0]
        total_paid = conn.execute(paid_sql, p_params).fetchone()[0]
        # 叠加期初应收/应付余额（仅应用期初后；direction 与 recon_type 一致：1应收 2应付）
        # 注：total_paid 已含核销期初的收付款，故期初余额直接相加，避免重复扣减
        _meta = conn.execute("SELECT applied FROM opening_meta WHERE id=1").fetchone()
        opening_bal = 0.0
        if _meta and _meta[0]:
            opening_bal = conn.execute(
                "SELECT COALESCE(SUM(balance),0) FROM opening_debt WHERE partner_id=? AND direction=?",
                (partner_id, recon_type)
            ).fetchone()[0]
        balance = round(total_order - total_paid + opening_bal, 2)

        partner = conn.execute("SELECT name FROM partners WHERE id=?", (partner_id,)).fetchone()
        partner_name = partner['name'] if partner else ''
        recon_no = _gen_recon_no(conn)

        row = conn.execute(
            """INSERT INTO reconciliations(recon_no,partner_id,partner_name,recon_type,
               period_start,period_end,total_order_amount,total_paid_amount,
               balance_amount,status,operator)
               VALUES(?,?,?,?,?,?,?,?,?,0,?)""",
            (recon_no, partner_id, partner_name, recon_type,
             period_start, period_end, total_order, total_paid, balance,
             data.get('operator', ''))
        )
        conn.commit()
        return {
            "ok": True, "id": row.lastrowid, "recon_no": recon_no,
            "total_order_amount": total_order,
            "total_paid_amount": total_paid,
            "balance_amount": balance
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()


def _recon_guard(conn, rid, allow):
    row = conn.execute("SELECT audit_status FROM reconciliations WHERE id=?", (rid,)).fetchone()
    if not row:
        raise HTTPException(404, "对账单不存在")
    if row['audit_status'] not in allow:
        raise HTTPException(400, "该对账单已审核/已作废，不能修改。请先反审核或作废。")
    return row

@router.post("/reconciliations/{rid}/approve")
def approve_reconciliation(rid: int):
    conn = get_conn(); _recon_guard(conn, rid, (0,))
    conn.execute("UPDATE reconciliations SET audit_status=1,status=1 WHERE id=?", (rid,))
    conn.commit(); conn.close(); return {"ok": True}

@router.post("/reconciliations/{rid}/unapprove")
def unapprove_reconciliation(rid: int):
    conn = get_conn(); _recon_guard(conn, rid, (1,))
    conn.execute("UPDATE reconciliations SET audit_status=0,status=0 WHERE id=?", (rid,))
    conn.commit(); conn.close(); return {"ok": True}

@router.post("/reconciliations/{rid}/void")
def void_reconciliation(rid: int):
    conn = get_conn(); _recon_guard(conn, rid, (0, 1))
    conn.execute("UPDATE reconciliations SET audit_status=2 WHERE id=?", (rid,))
    conn.commit(); conn.close(); return {"ok": True}

@router.put("/reconciliations/{rid}/confirm")
def confirm_reconciliation(rid: int, data: dict):
    """确认/标记争议对账单"""
    conn = get_conn()
    _recon_guard(conn, rid, (0,))
    status = int(data.get('status', 1))  # 1已确认 2有争议
    conn.execute(
        "UPDATE reconciliations SET status=?,remark=? WHERE id=?",
        (status, data.get('remark', ''), rid)
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@router.delete("/reconciliations/{rid}")
def delete_reconciliation(rid: int):
    conn = get_conn()
    _recon_guard(conn, rid, (0,))
    conn.execute("DELETE FROM reconciliations WHERE id=?", (rid,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ══════════════════════════════════════════
# 收付款统计（用于工作台）
# ══════════════════════════════════════════
@router.get("/finance/summary")
def finance_summary():
    conn = get_conn()
    today = datetime.date.today().isoformat()
    month_start = today[:7] + "-01"
    # 本月收款
    recv = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM payments WHERE pay_type=1 AND status=1 AND audit_status!=2 AND pay_date>=? AND pay_date<=?",
        (month_start, today)
    ).fetchone()[0]
    # 本月付款
    paid = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM payments WHERE pay_type=2 AND status=1 AND audit_status!=2 AND pay_date>=? AND pay_date<=?",
        (month_start, today)
    ).fetchone()[0]
    # 银行账户汇总余额
    bank_balance = conn.execute(
        "SELECT COALESCE(SUM(balance),0) FROM bank_accounts WHERE status=1"
    ).fetchone()[0]
    # 待收款（销售单已审核未完全收款）
    ar = conn.execute(
        """SELECT COALESCE(SUM(so.total_amount),0) - COALESCE(
            (SELECT SUM(p.amount) FROM payments p WHERE p.pay_type=1 AND p.status=1 AND p.audit_status!=2),0)
           FROM sale_orders so WHERE so.status>=2"""
    ).fetchone()[0]
    # 待付款（采购单已审核未完全付款）
    ap = conn.execute(
        """SELECT COALESCE(SUM(po.total_amount),0) - COALESCE(
            (SELECT SUM(p.amount) FROM payments p WHERE p.pay_type=2 AND p.status=1 AND p.audit_status!=2),0)
           FROM purchase_orders po WHERE po.status>=2"""
    ).fetchone()[0]
    # 叠加期初应收/应付（仅在「应用期初」后纳入余额）
    # 注：基础 ar/ap 已减去全部收款/付款（含核销期初的款项），故期初余额直接相加即可，避免重复扣减
    _meta = conn.execute("SELECT applied FROM opening_meta WHERE id=1").fetchone()
    _applied = bool(_meta and _meta[0])
    opening_ar = conn.execute("SELECT COALESCE(SUM(balance),0) FROM opening_debt WHERE direction=1").fetchone()[0] if _applied else 0
    opening_ap = conn.execute("SELECT COALESCE(SUM(balance),0) FROM opening_debt WHERE direction=2").fetchone()[0] if _applied else 0
    ar = round((ar or 0) + (opening_ar or 0), 2)
    ap = round((ap or 0) + (opening_ap or 0), 2)
    conn.close()
    return {
        "month_recv": recv,
        "month_paid": paid,
        "bank_balance": bank_balance,
        "ar_balance": max(ar, 0),   # 应收余额
        "ap_balance": max(ap, 0),   # 应付余额
    }


# ══════════════════════════════════════════
# 费用单据
# ══════════════════════════════════════════
EXPENSE_CATEGORIES = ["办公费用", "财务费用", "差旅费", "招待费", "水电费", "房租",
                      "工资薪金", "通讯费", "维修费", "运输费", "广告费", "其他"]


def _gen_expense_no(conn) -> str:
    """生成费用单号  FY-YYYYMMDD-NNNN"""
    today = datetime.date.today().strftime('%Y%m%d')
    pat = f"FY-{today}-%"
    row = conn.execute(
        "SELECT expense_no FROM expenses WHERE expense_no LIKE ? ORDER BY expense_no DESC LIMIT 1",
        (pat,)).fetchone()
    seq = 1
    if row:
        try:
            seq = int(row[0].split('-')[-1]) + 1
        except Exception:
            pass
    return f"FY-{today}-{seq:04d}"


class ExpenseIn(BaseModel):
    expense_date: Optional[str] = ""
    category: Optional[str] = "办公费用"
    amount: float = 0
    pay_account_id: Optional[int] = 0
    business_person: Optional[str] = ""
    remark: Optional[str] = ""


@router.get("/expense/categories")
def expense_categories():
    """费用类别列表"""
    return EXPENSE_CATEGORIES


@router.get("/expenses")
def list_expenses(month: str = "", category: str = "", keyword: str = "",
                 user: dict = Depends(get_current_user)):
    """费用单据列表（可按月份/类别/关键字筛选）"""
    conn = get_conn()
    sql = """SELECT e.*, COALESCE(b.name,'') as pay_account_name
        FROM expenses e LEFT JOIN bank_accounts b ON e.pay_account_id=b.id WHERE 1=1"""
    params = []
    if month:
        sql += " AND strftime('%Y-%m', e.expense_date)=?"
        params.append(month)
    if category:
        sql += " AND e.category=?"
        params.append(category)
    if keyword:
        sql += " AND (e.expense_no LIKE ? OR e.remark LIKE ? OR e.business_person LIKE ?)"
        params += [f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"]
    sql += " ORDER BY e.expense_date DESC, e.id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/expenses")
def create_expense(data: ExpenseIn, user: dict = Depends(get_current_user)):
    conn = get_conn()
    try:
        assert_dates_open(conn, data.expense_date, label="费用单据")
        no = _gen_expense_no(conn)
        conn.execute("""INSERT INTO expenses(expense_no,expense_date,category,amount,pay_account_id,remark,operator,business_person)
            VALUES(?,?,?,?,?,?,?,?)""",
            (no, data.expense_date, data.category, data.amount or 0, data.pay_account_id or 0,
             data.remark or '', user['display_name'], data.business_person or ''))
        conn.commit()
        return {"ok": True, "expense_no": no}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()


@router.get("/expenses/{eid}")
def get_expense(eid: int, user: dict = Depends(get_current_user)):
    conn = get_conn()
    row = conn.execute(
        "SELECT e.*, COALESCE(b.name,'') as pay_account_name FROM expenses e "
        "LEFT JOIN bank_accounts b ON e.pay_account_id=b.id WHERE e.id=?",
        (eid,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "费用单据不存在")
    return dict(row)


def _expense_guard(conn, eid, allow):
    row = conn.execute("SELECT audit_status FROM expenses WHERE id=?", (eid,)).fetchone()
    if not row:
        raise HTTPException(404, "费用单据不存在")
    if row['audit_status'] not in allow:
        raise HTTPException(400, "该费用单已审核/已作废，不能修改。请先反审核或作废。")
    return row

@router.post("/expenses/{eid}/approve")
def approve_expense(eid: int, user: dict = Depends(get_current_user)):
    conn = get_conn(); row = _expense_guard(conn, eid, (0,))
    assert_dates_open(conn, row['expense_date'], label="费用单据")
    conn.execute("UPDATE expenses SET audit_status=1 WHERE id=?", (eid,))
    conn.commit(); conn.close(); return {"ok": True}

@router.post("/expenses/{eid}/unapprove")
def unapprove_expense(eid: int, user: dict = Depends(get_current_user)):
    conn = get_conn(); row = _expense_guard(conn, eid, (1,))
    assert_dates_open(conn, row['expense_date'], label="费用单据")
    conn.execute("UPDATE expenses SET audit_status=0 WHERE id=?", (eid,))
    conn.commit(); conn.close(); return {"ok": True}

@router.post("/expenses/{eid}/void")
def void_expense(eid: int, user: dict = Depends(get_current_user)):
    conn = get_conn(); row = _expense_guard(conn, eid, (0, 1))
    assert_dates_open(conn, row['expense_date'], label="费用单据")
    conn.execute("UPDATE expenses SET audit_status=2 WHERE id=?", (eid,))
    conn.commit(); conn.close(); return {"ok": True}

@router.put("/expenses/{eid}")
def update_expense(eid: int, data: ExpenseIn, user: dict = Depends(get_current_user)):
    conn = get_conn()
    try:
        _expense_guard(conn, eid, (0,))
        assert_dates_open(conn, data.expense_date, label="费用单据")
        conn.execute("""UPDATE expenses SET expense_date=?,category=?,amount=?,pay_account_id=?,remark=?,business_person=?
            WHERE id=?""",
            (data.expense_date, data.category, data.amount or 0, data.pay_account_id or 0,
             data.remark or '', data.business_person or '', eid))
        conn.commit()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()


@router.delete("/expenses/{eid}")
def delete_expense(eid: int, user: dict = Depends(get_current_user)):
    conn = get_conn()
    _expense_guard(conn, eid, (0,))
    row = conn.execute("SELECT expense_date FROM expenses WHERE id=?", (eid,)).fetchone()
    if row:
        assert_dates_open(conn, row['expense_date'], label="费用单据")
    conn.execute("DELETE FROM expenses WHERE id=?", (eid,))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.get("/expense/report")
def expense_report(year: str = "", month: str = "", user: dict = Depends(get_current_user)):
    """月度费用报表：含手动费用单据 + 入库单/出库单手续费"""
    conn = get_conn()
    today = datetime.date.today()
    if not year:
        year = today.strftime('%Y')
    if not month:
        month = today.strftime('%Y-%m')
    # 全年逐月费用总额
    months = []
    for m in range(1, 13):
        ym = f"{year}-{m:02d}"
        exp = conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE strftime('%Y-%m',expense_date)=?", (ym,)).fetchone()[0]
        pf = conn.execute("SELECT COALESCE(SUM(fee),0) FROM purchase_orders WHERE strftime('%Y-%m',order_date)=?", (ym,)).fetchone()[0]
        sf = conn.execute("SELECT COALESCE(SUM(fee),0) FROM sale_orders WHERE strftime('%Y-%m',order_date)=?", (ym,)).fetchone()[0]
        months.append({"month": ym, "total": round(exp + pf + sf, 2)})
    # 选中月份分类明细
    cats = []
    for r in conn.execute(
        "SELECT category, COALESCE(SUM(amount),0) as amt FROM expenses "
        "WHERE strftime('%Y-%m',expense_date)=? GROUP BY category ORDER BY amt DESC", (month,)).fetchall():
        cats.append({"category": r['category'], "amount": round(r['amt'], 2)})
    pf = conn.execute("SELECT COALESCE(SUM(fee),0) FROM purchase_orders WHERE strftime('%Y-%m',order_date)=? AND fee>0", (month,)).fetchone()[0]
    sf = conn.execute("SELECT COALESCE(SUM(fee),0) FROM sale_orders WHERE strftime('%Y-%m',order_date)=? AND fee>0", (month,)).fetchone()[0]
    if pf:
        cats.append({"category": "采购手续费(入库单)", "amount": round(pf, 2)})
    if sf:
        cats.append({"category": "销售手续费(出库单)", "amount": round(sf, 2)})
    total = round(sum(c['amount'] for c in cats), 2)
    conn.close()
    return {"year": year, "month": month, "months": months,
            "categories": cats, "purchase_fee": round(pf, 2),
            "sale_fee": round(sf, 2), "total": total}


# ══════════════════════════════════════════
# 介绍方提成（佣金）管理
# ══════════════════════════════════════════
# 业务口径：
#   靠介绍成交的单子要给介绍方提成（好处费）。提成按「谈定总额 + 税点」拆成
#   代扣税额与实付金额——例：谈定 2000、税点 4% → 代扣 80、实付 1920。
#   提成在出库时已按「扣点后实付金额」冲减销售单毛利（sale_orders.gross_profit），
#   即谈定 2000、税点 4% → 代扣 80、成本记实付 1920。
#   所以本模块只负责「看得到 + 付得掉 + 有流水」，支付环节不再生成费用单，
#   否则同一笔佣金会在利润里被扣两次。


def _gen_commission_pay_no(conn) -> str:
    """生成提成支付单号 TC-YYYYMMDD-NNNN"""
    today = datetime.date.today().strftime('%Y%m%d')
    pat = f"TC-{today}-%"
    row = conn.execute(
        "SELECT pay_no FROM commission_payments WHERE pay_no LIKE ? ORDER BY pay_no DESC LIMIT 1",
        (pat,)).fetchone()
    seq = 1
    if row:
        try:
            seq = int(row[0].split('-')[-1]) + 1
        except Exception:
            pass
    return f"TC-{today}-{seq:04d}"


@router.get("/commissions")
def list_commissions(pay_status: int = -1, referrer_id: int = -1, keyword: str = "",
                     user: dict = Depends(get_current_user)):
    """提成台账：所有登记了介绍方提成的销售单（出库前也能看到，便于提前安排资金）"""
    conn = get_conn()
    sql = """SELECT o.id, o.order_no, o.order_date, o.status,
        o.referrer_id, o.referrer_name,
        COALESCE(o.commission_amount,0) commission_amount,
        COALESCE(o.commission_tax_rate,0) commission_tax_rate,
        COALESCE(o.commission_tax,0) commission_tax,
        COALESCE(o.commission_payable,0) commission_payable,
        COALESCE(o.commission_paid,0) commission_paid,
        COALESCE(o.commission_status,0) commission_status,
        COALESCE(o.untax_amount,0) untax_amount,
        COALESCE(o.total_amount,0) total_amount,
        COALESCE(o.cost_amount,0) cost_amount,
        COALESCE(o.gross_profit,0) gross_profit,
        COALESCE(c.name,'') customer_name, COALESCE(p.name,'') project_name
        FROM sale_orders o
        LEFT JOIN partners c ON o.customer_id=c.id
        LEFT JOIN projects p ON o.project_id=p.id
        WHERE COALESCE(o.commission_amount,0) > 0"""
    params = []
    if pay_status >= 0:
        sql += " AND COALESCE(o.commission_status,0)=?"
        params.append(pay_status)
    if referrer_id >= 0:
        sql += " AND o.referrer_id=?"
        params.append(referrer_id)
    if keyword:
        sql += " AND (o.order_no LIKE ? OR COALESCE(o.referrer_name,'') LIKE ? OR COALESCE(c.name,'') LIKE ?)"
        params += [f"%{keyword}%"] * 3
    sql += " ORDER BY o.order_date DESC, o.id DESC"
    rows = conn.execute(sql, params).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d['unpaid'] = round(float(d['commission_payable'] or 0) - float(d['commission_paid'] or 0), 2)
        result.append(d)
    conn.close()
    return result


@router.get("/commissions/summary")
def commission_summary(user: dict = Depends(get_current_user)):
    """提成汇总：提成总额 / 代扣税 / 应付实付 / 已付 / 未付"""
    conn = get_conn()
    r = conn.execute("""SELECT
        COALESCE(SUM(commission_amount),0) total_amount,
        COALESCE(SUM(commission_tax),0) total_tax,
        COALESCE(SUM(commission_payable),0) total_payable,
        COALESCE(SUM(commission_paid),0) total_paid,
        COUNT(*) order_count
        FROM sale_orders WHERE COALESCE(commission_amount,0)>0""").fetchone()
    conn.close()
    payable = float(r['total_payable'] or 0)
    paid = float(r['total_paid'] or 0)
    return {
        "order_count": r['order_count'],
        "total_amount": round(float(r['total_amount'] or 0), 2),
        "total_tax": round(float(r['total_tax'] or 0), 2),
        "total_payable": round(payable, 2),
        "total_paid": round(paid, 2),
        "unpaid": round(payable - paid, 2),
    }


@router.post("/commissions/{sale_id}/pay")
def pay_commission(sale_id: int, data: dict, user: dict = Depends(get_current_user)):
    """支付介绍方提成（支持分次付清）。

    只动资金：写流水 + 累计已付 + 扣银行账户余额。
    不动毛利——提成在出库时已经整额冲减过利润，这里再扣就重复了。
    """
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM sale_orders WHERE id=?", (sale_id,)).fetchone()
        if not row:
            raise HTTPException(404, "销售单不存在")
        if float(row['commission_amount'] or 0) <= 0:
            raise HTTPException(400, "该销售单没有登记介绍方提成")
        if not (row['referrer_id'] or 0):
            raise HTTPException(400, "该单未指定介绍方，请先补录")

        pay_date = data.get('pay_date') or datetime.date.today().isoformat()
        assert_dates_open(conn, pay_date, label="提成支付")
        amount = round(float(data.get('amount', 0) or 0), 2)
        if amount <= 0:
            raise HTTPException(400, "支付金额必须大于 0")

        payable = float(row['commission_payable'] or 0)
        paid = float(row['commission_paid'] or 0)
        unpaid = round(payable - paid, 2)
        if amount > unpaid + 0.01:
            raise HTTPException(400, f"本次支付 {amount} 元超过剩余未付 {unpaid} 元")

        # 本次实付按税点还原出对应的提成总额与代扣税，便于对账
        rate = float(row['commission_tax_rate'] or 0)
        gross_part = round(amount / (1 - rate / 100.0), 2) if rate > 0 else amount
        tax_part = round(gross_part - amount, 2)

        bank_account_id = int(data.get('bank_account_id', 0) or 0)
        pay_no = _gen_commission_pay_no(conn)
        conn.execute("""INSERT INTO commission_payments(pay_no,sale_order_id,sale_order_no,
            referrer_id,referrer_name,pay_date,amount,tax_amount,gross_amount,
            bank_account_id,remark,operator)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pay_no, sale_id, row['order_no'], row['referrer_id'] or 0,
             row['referrer_name'] or '', pay_date, amount, tax_part, gross_part,
             bank_account_id, data.get('remark', ''), user['display_name']))

        new_paid = round(paid + amount, 2)
        new_status = 3 if new_paid >= payable - 0.01 else 2
        conn.execute(
            "UPDATE sale_orders SET commission_paid=?,commission_status=? WHERE id=?",
            (new_paid, new_status, sale_id))

        if bank_account_id:
            conn.execute("UPDATE bank_accounts SET balance=balance-? WHERE id=?",
                         (amount, bank_account_id))

        conn.commit()
        return {"ok": True, "pay_no": pay_no, "paid": new_paid,
                "unpaid": round(payable - new_paid, 2)}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(400, str(e))
    finally:
        conn.close()


@router.get("/commission_payments")
def list_commission_payments(sale_id: int = -1, referrer_id: int = -1,
                             user: dict = Depends(get_current_user)):
    """提成支付流水"""
    conn = get_conn()
    sql = """SELECT cp.*, COALESCE(b.name,'') bank_account_name
        FROM commission_payments cp
        LEFT JOIN bank_accounts b ON cp.bank_account_id=b.id
        WHERE cp.status=1"""
    params = []
    if sale_id >= 0:
        sql += " AND cp.sale_order_id=?"
        params.append(sale_id)
    if referrer_id >= 0:
        sql += " AND cp.referrer_id=?"
        params.append(referrer_id)
    sql += " ORDER BY cp.pay_date DESC, cp.id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _commission_payment_guard(conn, pid, allow):
    row = conn.execute("SELECT * FROM commission_payments WHERE id=?", (pid,)).fetchone()
    if not row:
        raise HTTPException(404, "支付记录不存在")
    if row['status'] != 1:
        raise HTTPException(400, "该支付记录已撤销")
    if row['audit_status'] not in allow:
        raise HTTPException(400, "该支付已审核/已作废，不能修改。请先反审核。")
    return row


@router.post("/commission_payments/{pid}/approve")
def approve_commission_payment(pid: int, user: dict = Depends(get_current_user)):
    conn = get_conn(); row = _commission_payment_guard(conn, pid, (0,))
    assert_dates_open(conn, row['pay_date'], label="提成支付")
    conn.execute("UPDATE commission_payments SET audit_status=1 WHERE id=?", (pid,))
    conn.commit(); conn.close(); return {"ok": True}


@router.post("/commission_payments/{pid}/unapprove")
def unapprove_commission_payment(pid: int, user: dict = Depends(get_current_user)):
    conn = get_conn(); row = _commission_payment_guard(conn, pid, (1,))
    assert_dates_open(conn, row['pay_date'], label="提成支付")
    conn.execute("UPDATE commission_payments SET audit_status=0 WHERE id=?", (pid,))
    conn.commit(); conn.close(); return {"ok": True}


@router.post("/commission_payments/{pid}/void")
def void_commission_payment(pid: int, user: dict = Depends(get_current_user)):
    """撤销提成支付：回退已付金额、恢复银行账户余额"""
    conn = get_conn()
    try:
        row = _commission_payment_guard(conn, pid, (0,))
        assert_dates_open(conn, row['pay_date'], label="提成支付")
        sale_id = row['sale_order_id']
        amt = float(row['amount'] or 0)
        so = conn.execute("SELECT * FROM sale_orders WHERE id=?", (sale_id,)).fetchone()
        if so:
            new_paid = round(float(so['commission_paid'] or 0) - amt, 2)
            if new_paid < 0:
                new_paid = 0
            payable = float(so['commission_payable'] or 0)
            new_status = 0
            if payable > 0:
                new_status = 3 if new_paid >= payable - 0.01 else (2 if new_paid > 0 else 1)
            conn.execute(
                "UPDATE sale_orders SET commission_paid=?,commission_status=? WHERE id=?",
                (new_paid, new_status, sale_id))
        if row['bank_account_id']:
            conn.execute("UPDATE bank_accounts SET balance=balance+? WHERE id=?",
                         (amt, row['bank_account_id']))
        conn.execute("UPDATE commission_payments SET status=0,audit_status=2 WHERE id=?", (pid,))
        conn.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(400, str(e))
    finally:
        conn.close()
