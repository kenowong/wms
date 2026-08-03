# -*- coding: utf-8 -*-
"""期末封账守卫：已封账（status>=1）的年份，禁止新建/修改/删除该年份的业务单据。

业务单据的"会计年份"由其业务日期（order_date / pay_date / invoice_date / req_date
/ check_date / expense_date）前 4 位决定。一旦某年封账，任何把业务日期落在该年的
写操作都会被拦截，确保历史年度数据不可被篡改。
"""
from fastapi import HTTPException


def get_closed_years(conn) -> set:
    """返回所有已封账（含已封账并结转）的年份集合"""
    rows = conn.execute(
        "SELECT fiscal_year FROM year_closings WHERE status>=1"
    ).fetchall()
    return set(r[0] for r in rows)


def year_of(date_str):
    """从日期字符串（'2026-07-30' 或 '2026-07-30 10:00:00'）提取年份；空/非法返回 None"""
    if not date_str:
        return None
    s = str(date_str).strip()
    if len(s) >= 4 and s[:4].isdigit():
        return int(s[:4])
    return None


def assert_dates_open(conn, *dates, label: str = "业务单据"):
    """
    校验给定的业务日期是否落在已封账年份。
    任意一个日期所属年份已封账，即抛出 HTTPException(400)。
    """
    closed = get_closed_years(conn)
    if not closed:
        return
    for d in dates:
        y = year_of(d)
        if y is not None and y in closed:
            raise HTTPException(
                400,
                f"{label}日期 {d} 所属 {y} 年已封账，禁止操作。"
                f"如需修改，请先在「期末处理（封账/结转）」中对该年执行反封账。"
            )
