# -*- coding: utf-8 -*-
"""期初建账 + 待收付单据 + 财务汇总 端到端自测

覆盖：
    期初录入(库存/欠款/银行/现金/资产) → 应用期初 → 生效校验
    → 重置 → 重新应用 → 提成回归（确认毛利口径未被破坏）

断言核心：
    期初库存写入库存台账；银行余额 SET；现金生成 acct_type=2 账户
    待收/待付单据里出现「期初欠款」行（order_type='opening'）
    财务汇总 ar/ap 已叠加期初欠款
    提成单毛利 = 不含税收入 − 成本 − 提成实付
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

DBDIR = os.path.join(HERE, '_test_data_opening')
if os.path.exists(DBDIR):
    shutil.rmtree(DBDIR)
os.makedirs(DBDIR)
os.environ['WMS_DB_PATH'] = os.path.join(DBDIR, 'test.db')
os.environ['WMS_OPEN_BROWSER'] = '0'
PORT = 8973

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
    raise RuntimeError(f'未找到 {key}={val}')


def as_list(x):
    if isinstance(x, list):
        return x
    if isinstance(x, dict):
        for k in ('rows', 'data', 'items', 'list'):
            if isinstance(x.get(k), list):
                return x[k]
    return []


fails = []


def check(name, cond, extra=''):
    print(('  PASS  ' if cond else '  FAIL  ') + name + (f'   {extra}' if extra else ''))
    if not cond:
        fails.append(name)


def main():
    TOKEN['t'] = req('POST', '/api/auth/login',
                     {'username': 'admin', 'password': 'admin123'})['token']
    print('== 登录成功 ==\n')

    # ── 基础档案 ──
    req('POST', '/api/partners', {'name': '客户甲公司', 'type': 2})
    req('POST', '/api/partners', {'name': '供应商乙', 'type': 1})
    ps = req('GET', '/api/partners')
    cust = find_by(ps, 'name', '客户甲公司')
    sup = find_by(ps, 'name', '供应商乙')

    req('POST', '/api/goods', {'code': 'OP001', 'name': '期初测试货', 'unit': '台',
                               'default_tax_rate': 13})
    req('POST', '/api/goods', {'code': 'TC002', 'name': '提成回归货', 'unit': '台',
                               'default_tax_rate': 13})
    g1 = find_by(req('GET', '/api/goods'), 'code', 'OP001')
    g2 = find_by(req('GET', '/api/goods'), 'code', 'TC002')
    wh = req('GET', '/api/warehouses')[0]

    req('POST', '/api/bank_accounts', {'code': 'OPBANK', 'name': '期初测试户',
                                       'balance': 0, 'opening_balance': 0})
    bk = find_by(req('GET', '/api/bank_accounts'), 'code', 'OPBANK')
    today = time.strftime('%Y-%m-%d')

    print('---- 1. 期初录入 ----')
    req('POST', '/api/opening/inventory', {'items': [
        {'goods_id': g1['id'], 'warehouse_id': wh['id'], 'qty': 10, 'avg_cost': 100,
         'remark': '开账库存'}]})
    req('POST', '/api/opening/debt', {'items': [
        {'partner_id': cust['id'], 'direction': 1, 'balance': 3000, 'remark': '期初应收'},
        {'partner_id': sup['id'], 'direction': 2, 'balance': 2000, 'remark': '期初应付'}]})
    req('POST', '/api/opening/bank', {'items': [{'bank_account_id': bk['id'], 'balance': 50000}]})
    req('POST', '/api/opening/cash', {'items': [{'holder': '备用金', 'balance': 2000}]})
    req('POST', '/api/opening/asset', {'items': [{'name': '运输货车', 'category': '车辆',
                                                  'spec': '5吨', 'original_value': 80000}]})

    op = req('GET', '/api/opening')
    check('期初未应用时 applied=False', op['applied'] is False)
    check('期初库存已保存', len(op['inventory']) == 1
          and abs(float(op['inventory'][0]['qty']) - 10) < 0.01)
    check('期初应收/应付已保存', len(op['debt']) == 2, f"debt={len(op['debt'])}")
    check('期初固定资产已保存', len(op['asset']) == 1)
    check('期初银行/现金已保存', len(op['bank']) == 1 and len(op['cash']) == 1)

    print('\n---- 2. 应用期初 ----')
    r = req('POST', '/api/opening/apply', {})
    check('应用期初成功', r.get('ok') is True, r.get('msg', '')[:50])

    op = req('GET', '/api/opening')
    check('应用后 applied=True', op['applied'] is True)
    check('记录了应用人/时间', bool(op['applied_at']) and bool(op['applied_by']),
          f"{op['applied_at']} / {op['applied_by']}")

    inv = as_list(req('GET', '/api/inventory'))
    hit = None
    for x in inv:
        if x.get('goods_id') == g1['id'] and x.get('warehouse_id') == wh['id']:
            hit = x
            break
    check('期初库存写入库存台账', hit is not None and abs(float(hit.get('qty', 0)) - 10) < 0.01,
          f"qty={hit.get('qty') if hit else 'N/A'}")

    logs = as_list(req('GET', '/api/inventory/logs'))
    oplog = [x for x in logs if x.get('biz_type') == 'opening']
    check('生成 biz_type=opening 的库存流水', len(oplog) >= 1)

    banks = req('GET', '/api/bank_accounts')
    b1 = find_by(banks, 'code', 'OPBANK')
    check('期初银行余额已 SET 为 50000', abs(float(b1['balance']) - 50000) < 0.01,
          f"balance={b1['balance']}")
    cash = [b for b in banks if b.get('acct_type') == 2]
    check('期初现金生成 acct_type=2 账户', len(cash) == 1 and cash[0]['name'] == '备用金',
          f"{[c['name'] for c in cash]}")

    print('\n---- 3. 待收/待付单据（期初欠款行）----')
    po1 = as_list(req('GET', '/api/finance/pending-orders?pay_type=1'))
    op1 = [x for x in po1 if x.get('order_type') == 'opening']
    check('待收单据含「期初欠款」行', len(op1) == 1, f"找到 {len(op1)} 条")
    check('期初应收未结 = 3000',
          bool(op1) and abs(float(op1[0].get('remaining', 0)) - 3000) < 0.01,
          f"remaining={op1[0].get('remaining') if op1 else 'N/A'}")
    check('期初欠款单号显示为「期初欠款」',
          bool(op1) and op1[0].get('order_no') == '期初欠款')

    po2 = as_list(req('GET', '/api/finance/pending-orders?pay_type=2'))
    op2 = [x for x in po2 if x.get('order_type') == 'opening']
    check('待付单据含「期初欠款」行', len(op2) == 1
          and abs(float(op2[0].get('remaining', 0)) - 2000) < 0.01,
          f"remaining={op2[0].get('remaining') if op2 else 'N/A'}")

    print('\n---- 4. 财务汇总叠加期初 ----')
    s = req('GET', '/api/finance/summary')
    check('应收余额含期初 3000', abs(float(s['ar_balance']) - 3000) < 0.01,
          f"ar_balance={s['ar_balance']}")
    check('应付余额含期初 2000', abs(float(s['ap_balance']) - 2000) < 0.01,
          f"ap_balance={s['ap_balance']}")

    print('\n---- 5. 重置期初（无业务单据时应允许）----')
    r = req('POST', '/api/opening/reset', {})
    check('无业务单据时可重置', r.get('ok') is True)
    op = req('GET', '/api/opening')
    check('重置后 applied=False', op['applied'] is False)
    po1 = as_list(req('GET', '/api/finance/pending-orders?pay_type=1'))
    check('重置后待收单据不再含期初行',
          not [x for x in po1 if x.get('order_type') == 'opening'])
    s = req('GET', '/api/finance/summary')
    check('重置后应收余额不含期初', abs(float(s['ar_balance'])) < 0.01,
          f"ar_balance={s['ar_balance']}")

    print('\n---- 6. 重新应用期初 ----')
    r = req('POST', '/api/opening/apply', {})
    check('重新应用期初成功', r.get('ok') is True)
    s = req('GET', '/api/finance/summary')
    check('重新应用后应收余额恢复含期初', abs(float(s['ar_balance']) - 3000) < 0.01,
          f"ar_balance={s['ar_balance']}")

    print('\n---- 7. 提成回归（确认毛利口径未破坏）----')
    req('POST', '/api/purchase', {
        'supplier_id': sup['id'], 'warehouse_id': wh['id'], 'order_date': today,
        'items': [{'goods_id': g2['id'], 'qty': 1, 'unit_price': 1000, 'tax_rate': 13}]})
    po = find_by(req('GET', '/api/purchase'), 'supplier_id', sup['id'])
    req('POST', f"/api/purchase/{po['id']}/submit")
    req('POST', f"/api/purchase/{po['id']}/approve")
    req('POST', f"/api/purchase/{po['id']}/instock")

    req('POST', '/api/sale', {
        'customer_id': cust['id'], 'warehouse_id': wh['id'], 'order_date': today,
        'referrer_id': cust['id'], 'commission_amount': 2000, 'commission_tax_rate': 4,
        'items': [{'goods_id': g2['id'], 'qty': 1, 'unit_price': 5000, 'tax_rate': 13}]})
    so = find_by(req('GET', '/api/sale'), 'customer_id', cust['id'])
    req('POST', f"/api/sale/{so['id']}/submit")
    req('POST', f"/api/sale/{so['id']}/approve")
    req('POST', f"/api/sale/{so['id']}/outstock")
    d = req('GET', f"/api/sale/{so['id']}")

    untax = float(d['untax_amount'])
    cost = float(d['cost_amount'])
    payable = float(d['commission_payable'])
    gross = float(d['gross_profit'])
    expect = round(untax - cost - payable, 2)
    print(f"  不含税收入={untax:.2f}  成本={cost:.2f}  提成实付={payable:.2f}  "
          f"毛利={gross:.2f}  期望={expect:.2f}")
    check('提成实付 = 2000−80 = 1920', abs(payable - 1920) < 0.01, f"payable={payable}")
    check('毛利 = 不含税收入 − 成本 − 提成实付', abs(gross - expect) < 0.02)

    s = req('GET', '/api/finance/summary')
    # 销售单 5000 已审核未收款 → 基础应收 5000；再叠加期初应收 3000
    check('应收余额 = 销售未收 5000 + 期初 3000 = 8000',
          abs(float(s['ar_balance']) - 8000) < 0.01, f"ar_balance={s['ar_balance']}")

    print('\n---- 8. 有业务单据后重置应被拒绝 ----')
    err = ''
    try:
        req('POST', '/api/opening/reset', {})
    except Exception as e:
        err = str(e)
    check('有业务单据时拒绝重置', '已存在业务单据' in err, err[:70])

    print('\n' + ('=' * 50))
    if fails:
        print(f'❌ 失败 {len(fails)} 项：')
        for f in fails:
            print('   - ' + f)
        return 1
    print('✅ 全部通过：期初录入/应用/生效、待收付期初行、财务叠加、重置、提成回归 均正确')
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
