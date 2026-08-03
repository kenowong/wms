# -*- coding: utf-8 -*-
"""API路由 - 系统管理：数据备份/恢复、商品导入导出、往来单位导入导出"""
import os
import io
import csv
import shutil
import sqlite3
import tempfile
import datetime
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse, FileResponse, Response
from database import get_conn, DB_PATH

router = APIRouter()

# ══════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════
def _backup_dir() -> str:
    """备份目录：与数据库同级的 wms_backups/ 文件夹"""
    d = os.path.join(os.path.dirname(DB_PATH), "wms_backups")
    os.makedirs(d, exist_ok=True)
    return d


def _resolve_backup_dir(target_dir: str = "") -> str:
    """解析备份目录：为空用默认 wms_backups/；否则校验绝对路径/可写/可创建"""
    if not target_dir:
        return _backup_dir()
    if not os.path.isabs(target_dir):
        raise HTTPException(400, "请填写绝对路径，例如 D:\\我的备份 或 /data/backup（不支持相对路径）")
    d = os.path.abspath(target_dir)
    if not os.path.isdir(d):
        try:
            os.makedirs(d, exist_ok=True)
        except Exception as e:
            raise HTTPException(400, f"无法创建目录：{d}（{e}）")
    if not os.access(d, os.W_OK):
        raise HTTPException(400, f"目录无写入权限：{d}")
    return d


def _csv_response(rows: list, headers: list, filename: str) -> Response:
    """生成 CSV 下载响应。
    优先 GBK 编码（国产 Excel / WPS 双击直接正确显示中文，零乱码）；
    仅当含 GBK 不支持的极生僻字符时，回退 UTF-8 + BOM（跨平台兼容）。"""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers, extrasaction='ignore', lineterminator='\r\n')
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    text = output.getvalue()
    try:
        content = text.encode('gbk')          # 中文 Windows 双击最稳
        charset = 'gbk'
    except UnicodeEncodeError:
        content = ('\ufeff' + text).encode('utf-8')   # 回退：极少数生僻字
        charset = 'utf-8'
    # 中文文件名需用 RFC 5987 编码，否则 Starlette 用 latin-1 编码会抛 UnicodeEncodeError
    disp = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=content,
        media_type=f'text/csv; charset={charset}',
        headers={'Content-Disposition': disp}
    )


# ══════════════════════════════════════════
# 数据备份
# ══════════════════════════════════════════
@router.get("/system/backup/list")
def backup_list():
    """列出所有备份文件"""
    d = _backup_dir()
    files = []
    for f in sorted(os.listdir(d), reverse=True):
        if f.endswith('.db'):
            fp = os.path.join(d, f)
            files.append({
                "name": f,
                "size": os.path.getsize(fp),
                "created_at": datetime.datetime.fromtimestamp(os.path.getmtime(fp)).strftime('%Y-%m-%d %H:%M:%S')
            })
    return files


@router.post("/system/backup/create")
def backup_create(target_dir: str = ""):
    """立即创建一个备份；target_dir 可指定其他保存目录（绝对路径）"""
    dest_dir = _resolve_backup_dir(target_dir)
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    dest = os.path.join(dest_dir, f"wms_backup_{ts}.db")
    # 使用 SQLite backup API 保证一致性
    src_conn = sqlite3.connect(DB_PATH)
    dst_conn = sqlite3.connect(dest)
    src_conn.backup(dst_conn)
    dst_conn.close()
    src_conn.close()
    size = os.path.getsize(dest)
    return {"ok": True, "name": f"wms_backup_{ts}.db", "size": size, "path": dest,
            "created_at": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}


@router.get("/system/backup/export")
def backup_export():
    """生成备份并通过浏览器下载（可在保存对话框中选择任意目录）"""
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    name = f"wms_backup_{ts}.db"
    dest = os.path.join(tempfile.gettempdir(), name)
    src_conn = sqlite3.connect(DB_PATH)
    dst_conn = sqlite3.connect(dest)
    src_conn.backup(dst_conn)
    dst_conn.close()
    src_conn.close()
    return FileResponse(dest, media_type='application/octet-stream',
                        filename=name,
                        headers={'Content-Disposition': f'attachment; filename="{name}"'})


@router.get("/system/backup/download/{filename}")
def backup_download(filename: str):
    """下载指定备份文件"""
    if '..' in filename or '/' in filename or '\\' in filename:
        raise HTTPException(400, "非法文件名")
    fp = os.path.join(_backup_dir(), filename)
    if not os.path.isfile(fp):
        raise HTTPException(404, "备份文件不存在")
    return FileResponse(fp, media_type='application/octet-stream',
                        headers={'Content-Disposition': f'attachment; filename="{filename}"'})


@router.delete("/system/backup/{filename}")
def backup_delete(filename: str):
    """删除指定备份文件"""
    if '..' in filename or '/' in filename or '\\' in filename:
        raise HTTPException(400, "非法文件名")
    fp = os.path.join(_backup_dir(), filename)
    if not os.path.isfile(fp):
        raise HTTPException(404, "备份文件不存在")
    os.remove(fp)
    return {"ok": True}


@router.post("/system/backup/restore/{filename}")
def backup_restore(filename: str):
    """将指定备份覆盖当前数据库（危险操作，谨慎调用）"""
    if '..' in filename or '/' in filename or '\\' in filename:
        raise HTTPException(400, "非法文件名")
    fp = os.path.join(_backup_dir(), filename)
    if not os.path.isfile(fp):
        raise HTTPException(404, "备份文件不存在")
    # 先备份当前库
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    pre_backup = os.path.join(_backup_dir(), f"wms_pre_restore_{ts}.db")
    shutil.copy2(DB_PATH, pre_backup)
    # 恢复
    src_conn = sqlite3.connect(fp)
    dst_conn = sqlite3.connect(DB_PATH)
    src_conn.backup(dst_conn)
    dst_conn.close()
    src_conn.close()
    return {"ok": True, "pre_backup": f"wms_pre_restore_{ts}.db"}


# ══════════════════════════════════════════
# 商品档案 导出 / 导入
# ══════════════════════════════════════════
# 导出表头（中文）+ 英文key→中文 映射；导入时反向映射，兼容中英文表头
GOODS_HEADER_MAP = {
    'code': '商品编码', 'name': '商品名称', 'spec': '规格', 'model': '型号',
    'brand_name': '品牌', 'category_name': '分类', 'unit': '主单位', 'unit2': '辅单位',
    'unit2_rate': '换算率', 'default_tax_rate': '默认税率', 'ref_purchase_price': '参考进价',
    'ref_sale_price': '参考售价', 'stock_min': '库存下限', 'stock_max': '库存上限',
    'status': '状态', 'remark': '备注'
}
GOODS_EXPORT_HEADERS = list(GOODS_HEADER_MAP.values())

GOODS_IMPORT_REQUIRED = ['name']

@router.get("/system/export/goods")
def export_goods():
    """导出全部商品为 CSV"""
    conn = get_conn()
    rows = conn.execute("""
        SELECT g.code, g.name, g.spec, g.model,
               COALESCE(b.name,'') as brand_name,
               COALESCE(c.name,'') as category_name,
               g.unit, g.unit2, g.unit2_rate, g.default_tax_rate,
               g.ref_purchase_price, g.ref_sale_price,
               g.stock_min, g.stock_max, g.status, g.remark
        FROM goods g
        LEFT JOIN brands b ON g.brand_id=b.id
        LEFT JOIN categories c ON g.category_id=c.id
        ORDER BY g.code
    """).fetchall()
    conn.close()
    data = [dict(r) for r in rows]
    data_cn = [{GOODS_HEADER_MAP[k]: v for k, v in r.items()} for r in data]
    ts = datetime.datetime.now().strftime('%Y%m%d')
    return _csv_response(data_cn, GOODS_EXPORT_HEADERS, f"商品档案_{ts}.csv")


@router.post("/system/import/goods")
async def import_goods(file: UploadFile = File(...)):
    """导入商品 CSV（跳过 code 重复行，按 code 更新或新增）"""
    content = await file.read()
    # 兼容 UTF-8 BOM 和 GBK
    try:
        text = content.decode('utf-8-sig')
    except Exception:
        text = content.decode('gbk', errors='replace')

    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        raise HTTPException(400, "CSV 文件为空或格式错误")

    # 表头兼容：中文表头 → 英文key（未知key原样保留，兼容旧英文表头）
    cn_to_en = {v: k for k, v in GOODS_HEADER_MAP.items()}
    norm_rows = []
    for row in rows:
        nr = {}
        for k, v in row.items():
            nk = k.strip() if isinstance(k, str) else k
            nr[cn_to_en.get(nk, nk)] = v
        norm_rows.append(nr)
    rows = norm_rows

    conn = get_conn()
    added = updated = skipped = 0
    errors = []

    for i, row in enumerate(rows, 2):   # 行号从2开始（第1行是表头）
        name = (row.get('name') or '').strip()
        if not name:
            errors.append(f"第{i}行：name 为空，已跳过")
            skipped += 1
            continue

        code = (row.get('code') or '').strip()
        # 查找品牌和分类 ID
        brand_name = (row.get('brand_name') or '').strip()
        category_name = (row.get('category_name') or '').strip()
        brand_id = 0
        category_id = 0

        if brand_name:
            r = conn.execute("SELECT id FROM brands WHERE name=?", (brand_name,)).fetchone()
            if r:
                brand_id = r[0]
            else:
                # 自动创建品牌
                cur = conn.execute("INSERT INTO brands(name) VALUES(?)", (brand_name,))
                brand_id = cur.lastrowid

        if category_name:
            r = conn.execute("SELECT id FROM categories WHERE name=?", (category_name,)).fetchone()
            if r:
                category_id = r[0]

        def _f(k, default=0.0):
            try:
                return float(row.get(k) or default)
            except Exception:
                return default

        def _i(k, default=1):
            try:
                return int(row.get(k) or default)
            except Exception:
                return default

        spec   = (row.get('spec') or '').strip()
        model  = (row.get('model') or '').strip()
        unit   = (row.get('unit') or '个').strip()
        unit2  = (row.get('unit2') or '').strip()
        try:
            unit2_rate = float(row.get('unit2_rate') or 0)
        except Exception:
            unit2_rate = 0
        remark = (row.get('remark') or '').strip()

        if code:
            exists = conn.execute("SELECT id FROM goods WHERE code=?", (code,)).fetchone()
        else:
            exists = None
            # 自动生成编码
            today = datetime.date.today().strftime('%Y%m%d')
            last = conn.execute(
                "SELECT code FROM goods WHERE code LIKE ? ORDER BY code DESC LIMIT 1",
                (f"G-{today}-%",)).fetchone()
            seq = (int(last[0].split('-')[-1]) + 1) if last else 1
            code = f"G-{today}-{seq:04d}"

        try:
            if exists:
                conn.execute("""UPDATE goods SET name=?,spec=?,model=?,brand_id=?,category_id=?,
                    unit=?,unit2=?,unit2_rate=?,default_tax_rate=?,ref_purchase_price=?,ref_sale_price=?,
                    stock_min=?,stock_max=?,status=?,remark=? WHERE code=?""",
                    (name, spec, model, brand_id, category_id, unit, unit2, unit2_rate,
                     _f('default_tax_rate', 13.0), _f('ref_purchase_price'),
                     _f('ref_sale_price'), _f('stock_min'), _f('stock_max', 99999),
                     _i('status'), remark, code))
                updated += 1
            else:
                conn.execute("""INSERT INTO goods
                    (code,name,spec,model,brand_id,category_id,unit,unit2,unit2_rate,default_tax_rate,
                     ref_purchase_price,ref_sale_price,stock_min,stock_max,status,remark)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (code, name, spec, model, brand_id, category_id, unit, unit2, unit2_rate,
                     _f('default_tax_rate', 13.0), _f('ref_purchase_price'),
                     _f('ref_sale_price'), _f('stock_min'), _f('stock_max', 99999),
                     _i('status'), remark))
                added += 1
        except Exception as e:
            errors.append(f"第{i}行({name})：{e}")
            skipped += 1
            continue

    conn.commit()
    conn.close()
    return {"ok": True, "added": added, "updated": updated, "skipped": skipped, "errors": errors}


@router.get("/system/template/goods")
def template_goods():
    """下载商品导入模板"""
    sample = [{
        'code': 'G-20260101-0001', 'name': '示例商品', 'spec': '标准规格', 'model': 'ABC-100',
        'brand_name': '', 'category_name': '', 'unit': '个', 'unit2': '', 'unit2_rate': 0,
        'default_tax_rate': 13, 'ref_purchase_price': 100, 'ref_sale_price': 150,
        'stock_min': 0, 'stock_max': 99999, 'status': 1, 'remark': ''
    }]
    sample_cn = [{GOODS_HEADER_MAP[k]: v for k, v in sample[0].items()}]
    return _csv_response(sample_cn, GOODS_EXPORT_HEADERS, "商品导入模板.csv")


# ══════════════════════════════════════════
# 往来单位 导出 / 导入
# ══════════════════════════════════════════
# 导出表头（中文）+ 英文key→中文 映射；导入时反向映射，兼容中英文表头
PARTNER_HEADER_MAP = {
    'code': '单位编码', 'name': '单位名称', 'type_name': '单位类型',
    'contact': '联系人', 'phone': '联系电话', 'address': '地址',
    'tax_no': '税号', 'bank_name': '开户银行', 'bank_account': '银行账号',
    'status': '状态', 'remark': '备注'
}
PARTNER_EXPORT_HEADERS = list(PARTNER_HEADER_MAP.values())

PARTNER_TYPE_MAP = {'供应商': 1, '客户': 2, '两者': 3}
PARTNER_TYPE_RMAP = {1: '供应商', 2: '客户', 3: '两者'}

@router.get("/system/export/partners")
def export_partners():
    """导出全部往来单位为 CSV"""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM partners ORDER BY code").fetchall()
    conn.close()
    data = []
    for r in rows:
        d = dict(r)
        d['type_name'] = PARTNER_TYPE_RMAP.get(d.get('type', 3), '两者')
        row_cn = {PARTNER_HEADER_MAP[k]: d.get(k) for k in PARTNER_HEADER_MAP}
        data.append(row_cn)
    ts = datetime.datetime.now().strftime('%Y%m%d')
    return _csv_response(data, PARTNER_EXPORT_HEADERS, f"往来单位_{ts}.csv")


@router.post("/system/import/partners")
async def import_partners(file: UploadFile = File(...)):
    """导入往来单位 CSV（按 name 去重，存在则更新）"""
    content = await file.read()
    try:
        text = content.decode('utf-8-sig')
    except Exception:
        text = content.decode('gbk', errors='replace')

    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        raise HTTPException(400, "CSV 文件为空或格式错误")

    # 表头兼容：中文表头 → 英文key（未知key原样保留，兼容旧英文表头）
    cn_to_en = {v: k for k, v in PARTNER_HEADER_MAP.items()}
    norm_rows = []
    for row in rows:
        nr = {}
        for k, v in row.items():
            nk = k.strip() if isinstance(k, str) else k
            nr[cn_to_en.get(nk, nk)] = v
        norm_rows.append(nr)
    rows = norm_rows

    conn = get_conn()
    added = updated = skipped = 0
    errors = []

    for i, row in enumerate(rows, 2):
        name = (row.get('name') or '').strip()
        if not name:
            errors.append(f"第{i}行：name 为空，已跳过")
            skipped += 1
            continue

        type_name = (row.get('type_name') or '两者').strip()
        ptype = PARTNER_TYPE_MAP.get(type_name, 3)
        code = (row.get('code') or '').strip()
        contact = (row.get('contact') or '').strip()
        phone   = (row.get('phone') or '').strip()
        address = (row.get('address') or '').strip()
        tax_no  = (row.get('tax_no') or '').strip()
        bank_name    = (row.get('bank_name') or '').strip()
        bank_account = (row.get('bank_account') or '').strip()
        remark  = (row.get('remark') or '').strip()
        status  = int(row.get('status') or 1)

        exists = conn.execute("SELECT id FROM partners WHERE name=?", (name,)).fetchone()

        if not code:
            if ptype == 1:
                prefix = 'SP'
            elif ptype == 2:
                prefix = 'CU'
            else:
                prefix = 'BP'
            today = datetime.date.today().strftime('%Y%m%d')
            last = conn.execute(
                "SELECT code FROM partners WHERE code LIKE ? ORDER BY code DESC LIMIT 1",
                (f"{prefix}-{today}-%",)).fetchone()
            seq = (int(last[0].split('-')[-1]) + 1) if last else 1
            code = f"{prefix}-{today}-{seq:04d}"

        try:
            if exists:
                conn.execute("""UPDATE partners SET type=?,contact=?,phone=?,address=?,
                    tax_no=?,bank_name=?,bank_account=?,status=?,remark=? WHERE name=?""",
                    (ptype, contact, phone, address, tax_no, bank_name, bank_account,
                     status, remark, name))
                updated += 1
            else:
                conn.execute("""INSERT INTO partners
                    (code,name,type,contact,phone,address,tax_no,bank_name,bank_account,status,remark)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (code, name, ptype, contact, phone, address, tax_no,
                     bank_name, bank_account, status, remark))
                added += 1
        except Exception as e:
            errors.append(f"第{i}行({name})：{e}")
            skipped += 1
            continue

    conn.commit()
    conn.close()
    return {"ok": True, "added": added, "updated": updated, "skipped": skipped, "errors": errors}


@router.get("/system/template/partners")
def template_partners():
    """下载往来单位导入模板"""
    sample = [{
        'code': '', 'name': '示例供应商', 'type_name': '供应商',
        'contact': '张三', 'phone': '13800000000', 'address': '北京市',
        'tax_no': '', 'bank_name': '', 'bank_account': '', 'status': 1, 'remark': ''
    }]
    sample_cn = [{PARTNER_HEADER_MAP[k]: v for k, v in sample[0].items()}]
    return _csv_response(sample_cn, PARTNER_EXPORT_HEADERS, "往来单位导入模板.csv")
