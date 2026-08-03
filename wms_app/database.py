# -*- coding: utf-8 -*-
"""数据库初始化与连接管理"""
import sqlite3
import os
import sys

def get_db_path():
    """获取数据库路径（优先读取环境变量 WMS_DB_PATH，用于 NAS 挂载数据目录）"""
    env_path = os.environ.get("WMS_DB_PATH", "")
    if env_path:
        return env_path
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, 'wms_data.db')

DB_PATH = get_db_path()

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn

def init_db():
    """建库建表"""
    conn = get_conn()
    c = conn.cursor()

    # ── 商品分类（支持三级，parent_id=0 为一级）──
    c.execute('''CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        parent_id INTEGER DEFAULT 0,
        level INTEGER DEFAULT 1,   -- 1/2/3 级
        sort_order INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''')

    # ── 品牌 ──
    c.execute('''CREATE TABLE IF NOT EXISTS brands (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        logo TEXT,
        sort_order INTEGER DEFAULT 0,
        status INTEGER DEFAULT 1,
        remark TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''')

    # ── 仓库 ──
    c.execute('''CREATE TABLE IF NOT EXISTS warehouses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        manager TEXT,
        status INTEGER DEFAULT 1,
        remark TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''')

    # ── 往来单位 ──
    c.execute('''CREATE TABLE IF NOT EXISTS partners (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE,
        name TEXT NOT NULL,
        type INTEGER DEFAULT 3,  -- 1供应商 2客户 3两者
        contact TEXT,
        phone TEXT,
        address TEXT,
        tax_no TEXT,
        bank_name TEXT,
        bank_account TEXT,
        status INTEGER DEFAULT 1,
        remark TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''')

    # ── 商品档案 ──
    c.execute('''CREATE TABLE IF NOT EXISTS goods (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        spec TEXT,
        model TEXT,
        barcode TEXT DEFAULT '',
        brand_id INTEGER DEFAULT 0,
        category_id INTEGER DEFAULT 0,
        unit TEXT DEFAULT '个',
        unit2 TEXT DEFAULT '',
        unit2_rate REAL DEFAULT 0,
        default_tax_rate REAL DEFAULT 13.0,
        ref_purchase_price REAL DEFAULT 0,
        ref_sale_price REAL DEFAULT 0,
        stock_min REAL DEFAULT 0,
        stock_max REAL DEFAULT 99999,
        status INTEGER DEFAULT 1,
        remark TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''')

    # ── 项目 ──
    c.execute('''CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        customer_id INTEGER,
        manager TEXT,
        start_date TEXT,
        end_date TEXT,
        contract_amount REAL DEFAULT 0,
        status INTEGER DEFAULT 1,  -- 1进行中 2已完成 3暂停 0取消
        remark TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime'))
    )''')

    # ── 项目任务/阶段（用于项目流程与甘特图） ──
    c.execute('''CREATE TABLE IF NOT EXISTS project_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        assignee TEXT,
        start_date TEXT,
        end_date TEXT,
        progress REAL DEFAULT 0,        -- 0~100
        status INTEGER DEFAULT 1,      -- 1未开始 2进行中 3已完成 4延期
        parent_id INTEGER DEFAULT 0,   -- 阶段分组(0=顶层任务)
        sort_order INTEGER DEFAULT 0,
        remark TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime'))
    )''')

    # ── 采购单主表 ──
    c.execute('''CREATE TABLE IF NOT EXISTS purchase_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_no TEXT NOT NULL UNIQUE,
        project_id INTEGER,
        supplier_id INTEGER,
        warehouse_id INTEGER,
        order_date TEXT,
        total_amount REAL DEFAULT 0,
        tax_amount REAL DEFAULT 0,
        untax_amount REAL DEFAULT 0,
        status INTEGER DEFAULT 0, -- 0草稿 1待审核 2已审核 3已入库 4已关闭
        invoice_need INTEGER DEFAULT 0,      -- 0无需开票 1需要开票
        invoice_type_req TEXT DEFAULT '',    -- 增值税发票/普通发票/收据
        operator TEXT,
        remark TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime'))
    )''')

    # ── 采购单明细 ──
    c.execute('''CREATE TABLE IF NOT EXISTS purchase_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        goods_id INTEGER NOT NULL,
        goods_name TEXT,
        spec TEXT,
        model TEXT,
        unit TEXT,
        qty REAL DEFAULT 0,
        unit_price REAL DEFAULT 0,
        tax_rate REAL DEFAULT 13.0,
        tax_amount REAL DEFAULT 0,
        untax_amount REAL DEFAULT 0,
        total_amount REAL DEFAULT 0,
        remark TEXT,
        FOREIGN KEY(order_id) REFERENCES purchase_orders(id)
    )''')

    # ── 销售单主表 ──
    c.execute('''CREATE TABLE IF NOT EXISTS sale_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_no TEXT NOT NULL UNIQUE,
        project_id INTEGER,
        customer_id INTEGER,
        warehouse_id INTEGER,
        order_date TEXT,
        total_amount REAL DEFAULT 0,
        tax_amount REAL DEFAULT 0,
        untax_amount REAL DEFAULT 0,
        cost_amount REAL DEFAULT 0,
        gross_profit REAL DEFAULT 0,
        status INTEGER DEFAULT 0, -- 0草稿 1待审核 2已审核 3已出库 4已关闭
        invoice_need INTEGER DEFAULT 0,      -- 0无需开票 1需要开票
        invoice_type_req TEXT DEFAULT '',    -- 增值税发票/普通发票/收据
        operator TEXT,
        remark TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime'))
    )''')

    # ── 销售单明细 ──
    c.execute('''CREATE TABLE IF NOT EXISTS sale_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        goods_id INTEGER NOT NULL,
        goods_name TEXT,
        spec TEXT,
        model TEXT,
        unit TEXT,
        qty REAL DEFAULT 0,
        unit_price REAL DEFAULT 0,
        tax_rate REAL DEFAULT 13.0,
        cost_price REAL DEFAULT 0,
        total_amount REAL DEFAULT 0,
        remark TEXT,
        FOREIGN KEY(order_id) REFERENCES sale_orders(id)
    )''')

    # ── 耗材领用单 ──
    c.execute('''CREATE TABLE IF NOT EXISTS requisitions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_no TEXT NOT NULL UNIQUE,
        project_id INTEGER NOT NULL,
        warehouse_id INTEGER,
        req_date TEXT,
        applicant TEXT,
        total_cost REAL DEFAULT 0,
        status INTEGER DEFAULT 0, -- 0草稿 1已提交 2已审批 3已出库
        remark TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''')

    # ── 耗材领用明细 ──
    c.execute('''CREATE TABLE IF NOT EXISTS requisition_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        goods_id INTEGER NOT NULL,
        goods_name TEXT,
        unit TEXT,
        qty REAL DEFAULT 0,
        cost_price REAL DEFAULT 0,
        total_cost REAL DEFAULT 0,
        remark TEXT,
        FOREIGN KEY(order_id) REFERENCES requisitions(id)
    )''')

    # ── 库存台账 ──
    c.execute('''CREATE TABLE IF NOT EXISTS inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        goods_id INTEGER NOT NULL,
        warehouse_id INTEGER NOT NULL,
        project_id INTEGER DEFAULT 0,
        qty REAL DEFAULT 0,
        avg_cost REAL DEFAULT 0,
        total_cost REAL DEFAULT 0,
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        UNIQUE(goods_id, warehouse_id, project_id)
    )''')

    # ── 库存流水 ──
    c.execute('''CREATE TABLE IF NOT EXISTS inventory_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        goods_id INTEGER NOT NULL,
        goods_name TEXT,
        warehouse_id INTEGER,
        warehouse_name TEXT,
        project_id INTEGER DEFAULT 0,
        project_name TEXT,
        biz_type TEXT,  -- purchase/sale/requisition/check/adjust
        biz_order_no TEXT,
        direction INTEGER DEFAULT 1,  -- 1入库 -1出库
        qty REAL DEFAULT 0,
        before_qty REAL DEFAULT 0,
        after_qty REAL DEFAULT 0,
        cost_price REAL DEFAULT 0,
        operator TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''')

    # ── 库存盘点单 ──
    c.execute('''CREATE TABLE IF NOT EXISTS inventory_checks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        check_no TEXT NOT NULL UNIQUE,
        warehouse_id INTEGER,
        project_id INTEGER DEFAULT 0,
        check_date TEXT,
        status INTEGER DEFAULT 0, -- 0草稿 1盘点中 2已完成
        operator TEXT,
        remark TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''')

    # ── 盘点明细 ──
    c.execute('''CREATE TABLE IF NOT EXISTS check_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        check_id INTEGER NOT NULL,
        goods_id INTEGER NOT NULL,
        goods_name TEXT,
        unit TEXT,
        system_qty REAL DEFAULT 0,
        actual_qty REAL DEFAULT 0,
        diff_qty REAL DEFAULT 0,
        cost_price REAL DEFAULT 0,
        diff_amount REAL DEFAULT 0,
        FOREIGN KEY(check_id) REFERENCES inventory_checks(id)
    )''')

    # ── 发票主表 ──
    c.execute('''CREATE TABLE IF NOT EXISTS invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_no TEXT,
        invoice_code TEXT,
        invoice_type TEXT DEFAULT '增值税普通发票',
        direction INTEGER DEFAULT 1,  -- 1进项 2出项
        project_id INTEGER DEFAULT 0,
        partner_id INTEGER,
        partner_name TEXT,
        invoice_date TEXT,
        tax_rate REAL DEFAULT 13.0,
        untax_amount REAL DEFAULT 0,
        tax_amount REAL DEFAULT 0,
        total_amount REAL DEFAULT 0,
        status TEXT DEFAULT '已开具',  -- 待开具/已开具/已认证/已作废
        remark TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime'))
    )''')

    # ── 发票-单据关联 ──
    c.execute('''CREATE TABLE IF NOT EXISTS invoice_relations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id INTEGER NOT NULL,
        order_type TEXT,  -- purchase/sale
        order_id INTEGER,
        order_no TEXT,
        amount REAL DEFAULT 0,
        FOREIGN KEY(invoice_id) REFERENCES invoices(id)
    )''')

    # ── 银行账户 ──
    c.execute('''CREATE TABLE IF NOT EXISTS bank_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,          -- 账户名称/用途描述
        bank_name TEXT,              -- 开户行
        account_no TEXT,             -- 账号
        currency TEXT DEFAULT 'CNY',
        balance REAL DEFAULT 0,      -- 当前余额
        opening_balance REAL DEFAULT 0, -- 期初余额
        status INTEGER DEFAULT 1,
        remark TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''')

    # ── 收付款记录 ──
    c.execute('''CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pay_no TEXT NOT NULL UNIQUE,
        pay_type INTEGER DEFAULT 1,  -- 1收款 2付款
        pay_date TEXT,
        partner_id INTEGER,
        partner_name TEXT,
        bank_account_id INTEGER,     -- 收/付款银行账户
        amount REAL DEFAULT 0,       -- 本次金额
        fee REAL DEFAULT 0,          -- 手续费
        project_id INTEGER DEFAULT 0,
        remark TEXT,
        operator TEXT,
        status INTEGER DEFAULT 1,    -- 1正常 0已撤销
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''')

    # ── 收付款-单据关联 ──
    c.execute('''CREATE TABLE IF NOT EXISTS payment_relations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        payment_id INTEGER NOT NULL,
        order_type TEXT,             -- purchase/sale
        order_id INTEGER,
        order_no TEXT,
        write_off_amount REAL DEFAULT 0,  -- 核销金额
        FOREIGN KEY(payment_id) REFERENCES payments(id)
    )''')

    # ── 对账单 ──
    c.execute('''CREATE TABLE IF NOT EXISTS reconciliations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recon_no TEXT NOT NULL UNIQUE,
        partner_id INTEGER,
        partner_name TEXT,
        recon_type INTEGER DEFAULT 1,  -- 1应收对账 2应付对账
        period_start TEXT,
        period_end TEXT,
        total_order_amount REAL DEFAULT 0,   -- 期间单据总金额
        total_paid_amount REAL DEFAULT 0,    -- 已收/付款金额
        balance_amount REAL DEFAULT 0,       -- 未结余额
        status INTEGER DEFAULT 0,  -- 0未确认 1已确认 2有争议
        remark TEXT,
        operator TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''')

    # ── 费用单据 ──
    c.execute('''CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        expense_no TEXT NOT NULL UNIQUE,
        expense_date TEXT,
        category TEXT DEFAULT '办公费用',
        amount REAL DEFAULT 0,
        pay_account_id INTEGER DEFAULT 0,  -- 付款账户（可选）
        remark TEXT,
        operator TEXT,
        business_person TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''')

    # ── 计量单位字典 ──
    c.execute('''CREATE TABLE IF NOT EXISTS units (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        remark TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''')

    # ── 期末封账与结转 ──
    c.execute('''CREATE TABLE IF NOT EXISTS year_closings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fiscal_year INTEGER NOT NULL UNIQUE,   -- 封账年份
        status INTEGER DEFAULT 0,              -- 0未封账 1已封账 2已封账并结转
        closed_at TEXT,
        closed_by TEXT,
        carry_forwarded INTEGER DEFAULT 0,     -- 是否已完成结转
        carried_at TEXT,
        carried_by TEXT,
        remark TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS inventory_opening (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fiscal_year INTEGER NOT NULL,   -- 期初所属年份（结转后的新年份）
        goods_id INTEGER NOT NULL,
        warehouse_id INTEGER NOT NULL,
        project_id INTEGER DEFAULT 0,
        qty REAL DEFAULT 0,
        avg_cost REAL DEFAULT 0,
        total_cost REAL DEFAULT 0,
        UNIQUE(fiscal_year, goods_id, warehouse_id, project_id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS partner_opening (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fiscal_year INTEGER NOT NULL,
        partner_id INTEGER NOT NULL,
        partner_name TEXT,
        direction INTEGER DEFAULT 1,    -- 1应收(客户) 2应付(供应商)
        balance REAL DEFAULT 0,
        UNIQUE(fiscal_year, partner_id, direction)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS bank_opening (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fiscal_year INTEGER NOT NULL,
        bank_account_id INTEGER NOT NULL,
        account_name TEXT,
        balance REAL DEFAULT 0,
        UNIQUE(fiscal_year, bank_account_id)
    )''')

    # ── 用户与权限 ──
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        display_name TEXT DEFAULT '',
        role TEXT DEFAULT 'operator',  -- admin/operator/viewer
        status INTEGER DEFAULT 1,      -- 1启用 0禁用
        permissions TEXT DEFAULT '',   -- 逗号分隔的模块权限键（admin 恒为全权限）
        last_login TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''')

    # ── 初始化基础数据 ──
    c.execute("SELECT count(*) FROM warehouses")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO warehouses(code,name,manager) VALUES('WH001','主仓库','管理员')")

    # ── 兼容升级：brands 表（旧库可能没有）──
    try:
        c.execute('''CREATE TABLE IF NOT EXISTS brands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            logo TEXT,
            sort_order INTEGER DEFAULT 0,
            status INTEGER DEFAULT 1,
            remark TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )''')
    except Exception:
        pass

    # ── 兼容升级：银行账户/收付款/对账表（旧库）──
    for sql in [
        '''CREATE TABLE IF NOT EXISTS bank_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL, bank_name TEXT, account_no TEXT,
            currency TEXT DEFAULT 'CNY', balance REAL DEFAULT 0,
            opening_balance REAL DEFAULT 0, status INTEGER DEFAULT 1,
            remark TEXT, created_at TEXT DEFAULT (datetime('now','localtime')))''',
        '''CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, pay_no TEXT NOT NULL UNIQUE,
            pay_type INTEGER DEFAULT 1, pay_date TEXT, partner_id INTEGER,
            partner_name TEXT, bank_account_id INTEGER, amount REAL DEFAULT 0,
            fee REAL DEFAULT 0, project_id INTEGER DEFAULT 0, remark TEXT,
            operator TEXT, status INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now','localtime')))''',
        '''CREATE TABLE IF NOT EXISTS payment_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, payment_id INTEGER NOT NULL,
            order_type TEXT, order_id INTEGER, order_no TEXT,
            write_off_amount REAL DEFAULT 0)''',
        '''CREATE TABLE IF NOT EXISTS reconciliations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, recon_no TEXT NOT NULL UNIQUE,
            partner_id INTEGER, partner_name TEXT, recon_type INTEGER DEFAULT 1,
            period_start TEXT, period_end TEXT,
            total_order_amount REAL DEFAULT 0, total_paid_amount REAL DEFAULT 0,
            balance_amount REAL DEFAULT 0, status INTEGER DEFAULT 0,
            remark TEXT, operator TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')))''',
    ]:
        try:
            c.execute(sql)
        except Exception:
            pass

    # ── 兼容升级：旧数据库加字段 ──
    for tbl, col, defn in [
        ("goods", "model", "TEXT DEFAULT ''"),
        ("goods", "brand_id", "INTEGER DEFAULT 0"),
        # 计量单位：主单位/辅单位 + 换算率（1 主单位 = N 辅单位）
        ("goods", "unit2", "TEXT DEFAULT ''"),
        ("goods", "unit2_rate", "REAL DEFAULT 0"),
        ("goods", "barcode", "TEXT DEFAULT ''"),
        ("categories", "level", "INTEGER DEFAULT 1"),
        ("categories", "created_at", "TEXT DEFAULT (datetime('now','localtime'))"),
        ("purchase_items", "model", "TEXT DEFAULT ''"),
        ("sale_items", "model", "TEXT DEFAULT ''"),
        # 发票需求字段：入库单/出库单（0无需 1需要）
        ("purchase_orders", "invoice_need", "INTEGER DEFAULT 0"),
        ("purchase_orders", "invoice_type_req", "TEXT DEFAULT ''"),
        ("sale_orders", "invoice_need", "INTEGER DEFAULT 0"),
        ("sale_orders", "invoice_type_req", "TEXT DEFAULT ''"),
        # 单据追溯：执行人（登录账号，后端强制写入）+ 业务人员（可下拉选择）
        ("purchase_orders", "business_person", "TEXT DEFAULT ''"),
        ("sale_orders", "business_person", "TEXT DEFAULT ''"),
        # 费用：入库单/出库单手续费（计入公司费用）
        ("purchase_orders", "fee", "REAL DEFAULT 0"),
        ("sale_orders", "fee", "REAL DEFAULT 0"),
        ("requisitions", "operator", "TEXT DEFAULT ''"),
        ("inventory_checks", "business_person", "TEXT DEFAULT ''"),
        ("users", "permissions", "TEXT DEFAULT ''"),
        # 审核状态列（统一 audit_status：0草稿 1已审核 2已作废）；盘点用自身 status 字段不再加
        ("invoices", "audit_status", "INTEGER DEFAULT 0"),
        # 发票图片：存磁盘路径（相对数据目录）+ 识别原始串（二维码/ocr 原始内容，便于追溯）
        ("invoices", "image_path", "TEXT DEFAULT ''"),
        ("invoices", "ocr_raw", "TEXT DEFAULT ''"),
        ("payments", "audit_status", "INTEGER DEFAULT 0"),
        ("expenses", "audit_status", "INTEGER DEFAULT 0"),
        ("reconciliations", "audit_status", "INTEGER DEFAULT 0"),
    ]:
        try:
            c.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {defn}")
        except Exception:
            pass  # 字段已存在则跳过

    # ── 兼容升级：商品条码索引（提升扫码查询速度）──
    try:
        c.execute("CREATE INDEX IF NOT EXISTS idx_goods_barcode ON goods(barcode)")
    except Exception:
        pass

    # ── 兼容升级：users 表（旧库）──
    try:
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            display_name TEXT DEFAULT '',
            role TEXT DEFAULT 'operator',
            status INTEGER DEFAULT 1,
            last_login TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )''')
    except Exception:
        pass

    # ── 初始化默认管理员账号（admin/admin123）──
    c.execute("SELECT count(*) FROM users")
    if c.fetchone()[0] == 0:
        import hashlib
        pwd_hash = hashlib.sha256("admin123".encode()).hexdigest()
        c.execute(
            "INSERT INTO users(username,password_hash,display_name,role) VALUES(?,?,?,?)",
            ("admin", pwd_hash, "管理员", "admin")
        )

    # ── 预置/补全常用计量单位（缺则补，不覆盖用户已有；旧库已存在部分单位时也补全其余）──
    _default_units = ["个","件","台","套","箱","包","瓶","支","盒","袋","根","条","把",
                      "卷","桶","罐","只","张","辆","米","厘米","毫米","千克","公斤","克",
                      "吨","升","毫升","平方","平方米","立方","立方米","批","份","双","打","组","对","轴"]
    for _u in _default_units:
        try:
            c.execute("INSERT INTO units(name) VALUES(?)", (_u,))
        except Exception:
            pass  # 已存在则跳过

    conn.commit()
    conn.close()
    print(f"数据库初始化完成: {DB_PATH}")
