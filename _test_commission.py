# -*- coding: utf-8 -*-
"""介绍方提成功能端到端自测

覆盖：介绍方建档 → 销售单登记提成(含税点) → 出库冲减毛利 → 提成支付 → 撤销回退
断言核心口径：
    代扣税 = 提成总额 × 税点
    实付   = 提成总额 − 代扣税
    毛利   = 不含税收入 − 销售成本 − 提成总额   （提成整额冲减，不重复扣）
"""
import os
import sys
import json
import shutil
import time
import threading
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, 'wms_app')
sys.path.insert(0, APP)

DBDIR = os.path.join(HERE, '_test_data_commission')
if os.path.exists(DBDIR):
    shutil.rmtree(DBDIR)
os.makedirs(DBDIR)
os.environ['WMS_DB_PATH'] = os.path.join(DBDIR, 'test.db')
os.environ['WMS_OPEN_BROWSER'] = '0'
PORT = 8971

import uvicorn
from main import app  # noqa: E402

TOKEN = {'t': ''}
BASE = f'http://127.0.0.1:{PORT}'


def req(method, path, data=None):
    body = json.dumps(data).encode('utf-8') if data is not None else None
    r = urllib.request.Request(BASE + path, data=body, method=method)
    r.add_header('Content-Type', 'application/json')
    if TOKEN['t']:
        r.add_header('X-Token', TOKEN['t'])
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            txt = resp.read().decode('utf-8')
            return json.loads(txt) if txt else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'{method} {path} -> {e.code}: {e.read().decode("utf-8")}')


def find_by(rows, key, val):
    for r in rows:
        if r.get(key) == val:
            return r
    raise RuntimeError(f'未找到 {key}={val} 的记录')


fails = []


def check(name, cond, extra=''):
    print(('  PASS  ' if cond else '  FAIL  ') + name + (f'   {extra}' if extra else ''))
    if not cond:
        fails.append(name)


def main():
    TOKEN['t'] = req('POST', '/api/auth/login',
                     {'username': 'admin', 'password': 'admin123'})['token']
    print('== 登录成功 ==')

    # ── 基础档案 ──
    req('POST', '/api/partners', {'name': '介绍人老王', 'type': 3, 'is_referrer': 1})
    req('POST', '/api/partners', {'name': '客户甲公司', 'type': 2})
    req('POST', '/api/partners', {'name': '供应商乙', 'type': 1})
    partners = req('GET', '/api/partners')
    ref = find_by(partners, 'name', '介绍人老王')
    cust = find_by(partners, 'name', '客户甲公司')
    sup = find_by(partners, 'name', '供应商乙')
    check('介绍方建档并标记 is_referrer', ref.get('is_referrer') == 1, f"is_referrer={ref.get('is_referrer')}")

    req('POST', '/api/goods', {'code': 'TC001', 'name': '测试设备', 'unit': '台',
                               'default_tax_rate': 13, 'ref_purchase_price': 1000,
                               'ref_sale_price': 5000})
    goods = find_by(req('GET', '/api/goods'), 'code', 'TC001')
    wh = req('GET', '/api/warehouses')[0]

    req('POST', '/api/bank_accounts', {'code': 'BANK001', 'name': '招行基本户',
                                       'balance': 100000, 'opening_balance': 100000})
    bank = find_by(req('GET', '/api/bank_accounts'), 'code', 'BANK001')
    bank_start = float(bank['balance'])

    today = time.strftime('%Y-%m-%d')

    # ── 采购入库，形成库存成本 ──
    req('POST', '/api/purchase', {
        'supplier_id': sup['id'], 'warehouse_id': wh['id'], 'order_date': today,
        'items': [{'goods_id': goods['id'], 'qty': 1, 'unit_price': 1000, 'tax_rate': 13}]})
    po = find_by(req('GET', '/api/purchase'), 'supplier_id', sup['id'])
    req('POST', f"/api/purchase/{po['id']}/submit")
    req('POST', f"/api/purchase/{po['id']}/approve")
    req('POST', f"/api/purchase/{po['id']}/instock")

    # ── 销售单：售价 5000、成本约 1000、给介绍方提成 2000、税点 4% ──
    req('POST', '/api/sale', {
        'customer_id': cust['id'], 'warehouse_id': wh['id'], 'order_date': today,
        'referrer_id': ref['id'], 'commission_amount': 2000, 'commission_tax_rate': 4,
        'items': [{'goods_id': goods['id'], 'qty': 1, 'unit_price': 5000, 'tax_rate': 13}]})
    so = find_by(req('GET', '/api/sale'), 'customer_id', cust['id'])
    check('销售单登记提成总额', abs(float(so['commission_amount']) - 2000) < 0.01,
          f"commission_amount={so['commission_amount']}")
    check('税点已保存', abs(float(so['commission_tax_rate']) - 4) < 0.01,
          f"tax_rate={so['commission_tax_rate']}")
    check('代扣税 = 2000×4% = 80', abs(float(so['commission_tax']) - 80) < 0.01,
          f"commission_tax={so['commission_tax']}")
    check('实付 = 2000−80 = 1920', abs(float(so['commission_payable']) - 1920) < 0.01,
          f"commission_payable={so['commission_payable']}")
    check('介绍方名称已冗余保存', so['referrer_name'] == '介绍人老王', f"referrer_name={so['referrer_name']}")

    # ── 审核出库 ──
    req('POST', f"/api/sale/{so['id']}/submit")
    req('POST', f"/api/sale/{so['id']}/approve")
    req('POST', f"/api/sale/{so['id']}/outstock")
    d = req('GET', f"/api/sale/{so['id']}")

    untax = float(d['untax_amount'])
    cost = float(d['cost_amount'])
    comm_amt = float(d['commission_amount'])
    comm_pay = float(d['commission_payable'])
    gross = float(d['gross_profit'])
    # 成本口径：提成按扣点后的实付金额计入成本（2000 谈定、4% 税点 → 成本记 1920）
    expect = round(untax - cost - comm_pay, 2)
    print(f'\n  收入(不含税)={untax:.2f}  成本={cost:.2f}  '
          f'提成(谈定)={comm_amt:.2f}  提成(实付/计入成本)={comm_pay:.2f}  '
          f'毛利={gross:.2f}  期望={expect:.2f}')
    check('毛利 = 不含税收入 − 成本 − 提成实付(扣点后)', abs(gross - expect) < 0.02)
    check('成本按实付 1920 而非谈定 2000', abs(comm_pay - 1920) < 0.01 and abs(comm_amt - 2000) < 0.01,
          f"payable={comm_pay}, amount={comm_amt}")
    check('提成确实冲减了利润（对比未扣提成）', abs(gross - (untax - cost)) > 1900,
          f"未扣提成时毛利={untax - cost:.2f}，实际={gross:.2f}")

    # ── 提成台账 ──
    rows = req('GET', '/api/commissions')
    c1 = find_by(rows, 'id', so['id'])
    check('提成台账能看到该单', c1 is not None)
    check('台账未付金额 = 1920', abs(float(c1['unpaid']) - 1920) < 0.01, f"unpaid={c1['unpaid']}")
    check('台账状态 = 待支付', c1['commission_status'] == 1, f"status={c1['commission_status']}")

    s = req('GET', '/api/commissions/summary')
    check('汇总：提成总额 2000', abs(float(s['total_amount']) - 2000) < 0.01, f"total={s['total_amount']}")
    check('汇总：代扣税 80', abs(float(s['total_tax']) - 80) < 0.01, f"tax={s['total_tax']}")
    check('汇总：应付 1920 / 未付 1920',
          abs(float(s['total_payable']) - 1920) < 0.01 and abs(float(s['unpaid']) - 1920) < 0.01,
          f"payable={s['total_payable']} unpaid={s['unpaid']}")

    # ── 支付提成 1000（分次付）──
    req('POST', f"/api/commissions/{so['id']}/pay", {
        'amount': 1000, 'pay_date': today, 'bank_account_id': bank['id'], 'remark': '微信转账'})
    d2 = req('GET', f"/api/sale/{so['id']}")
    check('已付累计 = 1000', abs(float(d2['commission_paid']) - 1000) < 0.01,
          f"paid={d2['commission_paid']}")
    check('状态 = 部分支付(2)', d2['commission_status'] == 2, f"status={d2['commission_status']}")
    check('毛利支付后不变（不重复扣）', abs(float(d2['gross_profit']) - gross) < 0.01,
          f"gross={d2['gross_profit']}")
    bank_now = float(find_by(req('GET', '/api/bank_accounts'), 'code', 'BANK001')['balance'])
    check('银行账户扣减 1000', abs((bank_start - bank_now) - 1000) < 0.01,
          f"余额 {bank_start} -> {bank_now}")

    pays = req('GET', '/api/commission_payments')
    check('支付流水已生成', len(pays) == 1, f"流水数={len(pays)}")
    p1 = pays[0]
    check('流水记录代扣税(按比例)', abs(float(p1['tax_amount']) - 41.67) < 0.05,
          f"tax_amount={p1['tax_amount']} (1000实付 ≈ 1041.67提成 - 41.67税)")

    # ── 撤销支付，验证回退 ──
    req('POST', f"/api/commission_payments/{p1['id']}/void")
    d3 = req('GET', f"/api/sale/{so['id']}")
    check('撤销后已付归零', abs(float(d3['commission_paid']) - 0) < 0.01, f"paid={d3['commission_paid']}")
    check('撤销后状态回到待支付', d3['commission_status'] == 1, f"status={d3['commission_status']}")
    bank_back = float(find_by(req('GET', '/api/bank_accounts'), 'code', 'BANK001')['balance'])
    check('撤销后银行余额恢复', abs(bank_back - bank_start) < 0.01, f"余额={bank_back}")

    # ── 利润统计（工作台）──
    dash = req('GET', '/api/stats/dashboard')
    check('工作台本月毛利已含提成冲减', abs(float(dash['profit_month']) - expect) < 0.02,
          f"profit_month={dash['profit_month']} 期望={expect}")

    # ── 超额支付应被拦截 ──
    err = ''
    try:
        req('POST', f"/api/commissions/{so['id']}/pay",
            {'amount': 99999, 'pay_date': today, 'bank_account_id': bank['id']})
    except Exception as e:
        err = str(e)
    check('超额支付被拒绝', '超过剩余未付' in err, err[:80])

    print('\n' + ('=' * 46))
    if fails:
        print(f'❌ 失败 {len(fails)} 项：')
        for f in fails:
            print('   - ' + f)
        return 1
    print('✅ 全部通过：提成登记 / 税点拆分 / 毛利冲减 / 支付 / 撤销 均正确')
    return 0


if __name__ == '__main__':
    threading.Thread(
        target=lambda: uvicorn.run(app, host='127.0.0.1', port=PORT, log_level='error'),
        daemon=True).start()
    time.sleep(3.5)
    code = 1
    try:
        code = main()
    except Exception as e:
        print('测试异常：', e)
        import traceback
        traceback.print_exc()
    sys.exit(code)
