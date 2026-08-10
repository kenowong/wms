# -*- coding: utf-8 -*-
"""打印模板与公司信息（打印抬头）

- print_templates 表：每种单据一套可编辑的 HTML 打印模板（占位符由前端填充）
- sys_config 表：存储公司信息（名称/地址/电话/税号），作为打印抬头
"""
from fastapi import APIRouter, Depends, HTTPException
from database import get_conn
from routers_auth import get_current_user

router = APIRouter()

# ── 单据类型 → 默认标题 ──
DOC_TITLES = {
    'purchase': '采购入库单',
    'sale': '销售出库单',
    'requisition': '耗材领用单',
    'check': '库存盘点单',
    'payment': '收付款单',
    'invoice': '发票',
    'expense': '费用单',
    'reconciliation': '对账单',
}

# ── 各单据的附加信息行（标签 + 占位符），占位符由前端填充 ──
DOC_INFO = {
    'purchase': [('项目', '{{project_name}}'), ('仓库', '{{warehouse_name}}'),
                 ('业务人员', '{{business_person}}'), ('税额', '{{tax_amount}}'), ('手续费', '{{fee}}')],
    'sale': [('项目', '{{project_name}}'), ('仓库', '{{warehouse_name}}'),
             ('业务人员', '{{business_person}}'), ('毛利', '{{gross_profit}}'),
             ('税额', '{{tax_amount}}'), ('手续费', '{{fee}}')],
    'requisition': [('项目', '{{project_name}}'), ('仓库', '{{warehouse_name}}'), ('领用人', '{{applicant}}')],
    'check': [('仓库', '{{warehouse_name}}'), ('业务人员', '{{business_person}}')],
    'payment': [('银行', '{{bank_account_name}}'), ('手续费', '{{fee}}'), ('类型', '{{pay_type_label}}')],
    'invoice': [('发票代码', '{{invoice_code}}'), ('发票类型', '{{invoice_type}}'), ('项目', '{{project_name}}'),
                ('不含税金额', '{{untax_amount}}'), ('税额', '{{tax_amount}}'), ('状态', '{{status}}')],
    'expense': [('类别', '{{category}}'), ('付款账户', '{{pay_account_name}}'), ('业务人员', '{{business_person}}')],
    'reconciliation': [('类型', '{{recon_type_label}}'), ('期间', '{{period_start}} ~ {{period_end}}'),
                       ('单据合计', '{{total_order_amount}}'), ('已收/已付', '{{total_paid_amount}}')],
}

# ── 需要渲染明细表（{{items}}）的单据 ──
HAS_ITEMS = {'purchase', 'sale', 'requisition', 'check', 'payment'}

# 公司信息字段（写入 sys_config）
COMPANY_KEYS = ['company_name', 'company_address', 'company_phone', 'company_tax_no']


def _build_content(doc_type):
    """拼接默认模板 HTML（占位符保持字面量，供前端替换）"""
    info = ''.join(
        '<div class="pt-info"><span class="pt-label">%s：</span>%s</div>' % (lbl, ph)
        for lbl, ph in DOC_INFO.get(doc_type, [])
    )
    items = '<div class="pt-items">{{items}}</div>' if doc_type in HAS_ITEMS else ''
    return (
        '<div class="pt-doc">'
        '<div class="pt-head">'
        '<div class="pt-company">{{company}}</div>'
        '<div class="pt-sub">{{company_address}}　{{company_phone}}　税号：{{company_tax_no}}</div>'
        '<div class="pt-title">{{doc_title}}</div>'
        '<div class="pt-meta">单号：{{doc_no}}　　日期：{{doc_date}}</div>'
        '</div>'
        '<div class="pt-parties"><div>往来单位：{{partner}}</div><div>经手人：{{operator}}</div></div>'
        + info
        + items
        + '<div class="pt-total">合计金额：{{amount}}</div>'
        '<div class="pt-remark">备注：{{remark}}</div>'
        '<div class="pt-foot"><div>制单人：{{operator}}</div><div>打印时间：{{print_time}}</div></div>'
        '</div>'
    )


DEFAULT_TEMPLATES = {
    dt: {'title': DOC_TITLES[dt], 'content': _build_content(dt)}
    for dt in DOC_TITLES
}


def ensure_print_tables():
    """建表并播种默认模板（仅当为空时）"""
    conn = get_conn()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS print_templates (
        doc_type TEXT PRIMARY KEY,
        title TEXT,
        content TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS sys_config (
        k TEXT PRIMARY KEY,
        v TEXT
    )''')
    for dt, tpl in DEFAULT_TEMPLATES.items():
        cur = conn.execute('SELECT 1 FROM print_templates WHERE doc_type=?', (dt,)).fetchone()
        if not cur:
            conn.execute(
                'INSERT INTO print_templates(doc_type, title, content) VALUES(?,?,?)',
                (dt, tpl['title'], tpl['content'])
            )
    conn.commit()
    conn.close()


# ══════════════════════════════════════════
# 打印模板接口
# ══════════════════════════════════════════
@router.get('/print/templates')
def list_templates():
    conn = get_conn()
    rows = conn.execute('SELECT doc_type, title, content FROM print_templates ORDER BY doc_type').fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get('/print/template/{doc_type}')
def get_template(doc_type: str):
    if doc_type not in DOC_TITLES:
        raise HTTPException(404, '未知单据类型')
    conn = get_conn()
    r = conn.execute(
        'SELECT doc_type, title, content FROM print_templates WHERE doc_type=?', (doc_type,)
    ).fetchone()
    conn.close()
    if not r:
        raise HTTPException(404, '模板不存在')
    return dict(r)


@router.put('/print/template/{doc_type}')
def save_template(doc_type: str, body: dict, user: dict = Depends(get_current_user)):
    if doc_type not in DOC_TITLES:
        raise HTTPException(400, '未知单据类型')
    title = (body.get('title') or DOC_TITLES[doc_type])[:50]
    content = body.get('content', '')
    conn = get_conn()
    conn.execute(
        '''INSERT INTO print_templates(doc_type, title, content)
           VALUES(?,?,?)
           ON CONFLICT(doc_type) DO UPDATE SET title=excluded.title, content=excluded.content''',
        (doc_type, title, content)
    )
    conn.commit()
    conn.close()
    return {'ok': True}


# ══════════════════════════════════════════
# 公司信息（打印抬头）
# ══════════════════════════════════════════
@router.get('/system/company')
def get_company():
    conn = get_conn()
    rows = conn.execute(
        "SELECT k, v FROM sys_config WHERE k LIKE 'company_%'"
    ).fetchall()
    conn.close()
    return {r['k']: r['v'] for r in rows}


@router.put('/system/company')
def save_company(body: dict, user: dict = Depends(get_current_user)):
    conn = get_conn()
    for k in COMPANY_KEYS:
        if k in body:
            conn.execute(
                '''INSERT INTO sys_config(k, v) VALUES(?,?)
                   ON CONFLICT(k) DO UPDATE SET v=excluded.v''',
                (k, str(body.get(k, '') or ''))
            )
    conn.commit()
    conn.close()
    return {'ok': True}
