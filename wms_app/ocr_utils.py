# -*- coding: utf-8 -*-
"""发票图片识别：二维码优先，文字 OCR 兜底（pytesseract + 中文词库）

- 标准增值税发票（专票/普票/电子发票）右下角均有二维码，内含发票代码/号码/日期/金额等结构化字段，
  二维码识别轻量、离线、准确率高，作为首选。
- 无二维码或二维码解析不全时，调用 Tesseract 做印刷体文字 OCR，再用正则提取关键字段作补充。
- Tesseract 二进制不在 PATH 时，自动尝试 exe 同级 `tesseract/` 目录；都没有则仅启用二维码识别。
"""
import os
import re
import uuid

from database import get_db_path

try:
    import cv2
    import numpy as np
    HAVE_CV2 = True
except Exception:
    HAVE_CV2 = False

try:
    import pytesseract
    HAVE_TESS = True
except Exception:
    HAVE_TESS = False

try:
    import fitz  # PyMuPDF：纯 Python 轮子，离线渲染 PDF 页为图片，无需 poppler 等系统组件
    HAVE_FITZ = True
except Exception:
    HAVE_FITZ = False


def _locate_tesseract():
    """返回可用的 tesseract 命令；找不到返回 None。

    查找顺序：
    1. 源码/打包目录下的 tesseract/ （开发态与 PyInstaller 单文件解包态 MEIPASS）
    2. 当前 exe 同级目录的 tesseract/ （Inno Setup 把 tesseract 装到程序目录时）
    3. 系统 PATH 中的 tesseract
    """
    if not HAVE_TESS:
        return None
    candidates = []
    # 1. 模块所在目录（开发态）或 _MEIPASS（单文件打包解包态）
    candidates.append(os.path.dirname(os.path.abspath(__file__)))
    # 2. 真正 exe 所在目录（Inno Setup 安装态）
    try:
        import sys
        if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
            candidates.append(os.path.dirname(os.path.abspath(sys.executable)))
    except Exception:
        pass
    for base in candidates:
        exe_cand = os.path.join(base, "tesseract", "tesseract.exe")
        if os.path.exists(exe_cand):
            tessdata = os.path.join(base, "tesseract", "tessdata")
            if os.path.isdir(tessdata):
                os.environ["TESSDATA_PREFIX"] = tessdata
            return exe_cand
    # 3. 依赖系统 PATH
    try:
        pytesseract.get_tesseract_version()
        return "tesseract"
    except Exception:
        return None


TESS_CMD = _locate_tesseract()
if TESS_CMD and TESS_CMD != "tesseract":
    try:
        pytesseract.pytesseract.tesseract_cmd = TESS_CMD
    except Exception:
        pass

ALLOWED_IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
ALLOWED_PDF_EXT = {".pdf"}
ALLOWED_EXT = ALLOWED_IMG_EXT | ALLOWED_PDF_EXT


def get_data_dir():
    return os.path.dirname(os.path.abspath(get_db_path()))


def get_upload_dir():
    d = os.path.join(get_data_dir(), "uploads", "invoices")
    os.makedirs(d, exist_ok=True)
    return d


def save_upload(filename, content):
    """保存上传图片，返回相对数据目录的路径（如 uploads/invoices/xxx.png）。"""
    ext = os.path.splitext(filename or "image.png")[1].lower()
    if ext not in ALLOWED_EXT:
        raise ValueError("不支持的文件格式：" + (ext or "未知") + "（仅支持 jpg/png/bmp/gif/webp/pdf）")
    name = uuid.uuid4().hex + ext
    path = os.path.join(get_upload_dir(), name)
    with open(path, "wb") as f:
        f.write(content)
    return os.path.relpath(path, get_data_dir()).replace("\\", "/")


# ───────────────────────────────────────────────────────────
# 二维码识别
# ───────────────────────────────────────────────────────────
def _decode_qr(img):
    if not HAVE_CV2:
        return ""
    try:
        det = cv2.QRCodeDetector()
        data, *_ = det.detectAndDecode(img)
        if data:
            return data
        # 多尺度 + 旋转尝试，提升模糊/倾斜二维码识别率
        for scale in (1.5, 0.75, 2.0):
            small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
            d2, *_ = det.detectAndDecode(small)
            if d2:
                return d2
        h, w = img.shape[:2]
        center = (w // 2, h // 2)
        for ang in (90, 180, 270):
            m = cv2.getRotationMatrix2D(center, ang, 1.0)
            rot = cv2.warpAffine(img, m, (w, h))
            d3, *_ = det.detectAndDecode(rot)
            if d3:
                return d3
    except Exception:
        pass
    return ""


def _norm_date(s):
    s = re.sub(r"\D", "", s)
    if len(s) == 8 and s[:2] in ("19", "20"):
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return ""


def _parse_qr(qr):
    """解析增值税发票二维码字符串（逗号分隔，字段顺序因票种而异，采用启发式提取）。"""
    res = {"invoice_no": "", "invoice_code": "", "invoice_date": "",
           "untax_amount": 0.0, "tax_amount": 0.0, "total_amount": 0.0}
    raw = (qr or "").strip()
    toks = [t.strip() for t in re.split(r"[,，;；]", raw) if t.strip()]
    dates, codes, nums, amounts, ints = [], [], [], [], []
    for t in toks:
        if re.fullmatch(r"\d{8}", t) and t[:2] in ("19", "20"):
            dates.append(t)
        elif re.fullmatch(r"\d{10,12}", t):
            codes.append(t)
        elif re.fullmatch(r"\d{8,10}", t):
            nums.append(t)
        elif re.fullmatch(r"\d+\.\d+", t):
            # 增值税发票金额字段一定带两位小数，仅把带小数点的 token 视为金额，
            # 避免把开头的标志位(如 01/10)或税号前缀误判为金额
            amounts.append(float(t))
        elif re.fullmatch(r"\d{3,9}", t):
            ints.append(float(t))
    if dates:
        res["invoice_date"] = _norm_date(dates[0])
    if codes:
        res["invoice_code"] = codes[0]
    if nums:
        res["invoice_no"] = nums[0]
    # 先剔除疑似税率(<1.0)的 token（新版全电二维码会在金额之间插入 0.13 这类税率），
    # 避免金额顺序错乱；若无 >=1 的金额则保留原样兜底
    amts = [a for a in amounts if a >= 1.0] or amounts
    if amts:
        # 二维码中金额顺序：不含税金额、税额、价税合计
        # 注意：不含税金额(如 604.62) 往往大于 税额(如 78.38)，不能按大小排序假设
        if len(amts) >= 3:
            untax, tax, total = amts[0], amts[1], amts[2]
            if total < untax or total < tax or abs((untax + tax) - total) > 0.5:
                s = sorted(amts)
                untax, tax, total = s[0], s[1], s[-1]
            res["untax_amount"] = round(untax, 2)
            res["tax_amount"] = round(tax, 2)
            res["total_amount"] = round(total, 2)
        elif len(amts) == 2:
            s = sorted(amts)
            res["tax_amount"] = round(s[0], 2)
            res["total_amount"] = round(s[1], 2)
            res["untax_amount"] = round(res["total_amount"] - res["tax_amount"], 2)
        else:
            res["total_amount"] = round(amts[0], 2)
    elif len(ints) >= 3:
        # 极少数无小数金额：用整数金额兜底（已排除标志位/税号）
        s = sorted(ints)
        res["untax_amount"] = round(s[0], 2)
        res["tax_amount"] = round(s[1], 2)
        res["total_amount"] = round(s[-1], 2)
    return res


# ───────────────────────────────────────────────────────────
# 文字 OCR（Tesseract）
# ───────────────────────────────────────────────────────────
def _ocr_text(img):
    if not TESS_CMD or not HAVE_CV2:
        return ""
    try:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
        # 简单二值化提升印刷体识别率
        _, bin_img = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        txt = pytesseract.image_to_string(bin_img, lang="chi_sim+eng")
        return txt or ""
    except Exception:
        return ""


def _digits_after_label(text, label, minlen, maxlen):
    """在 OCR 文本中按标签抓取后面的号码/代码，容忍 OCR 在数字间插入的空格与标签错位。

    - label 用正则传入，允许中文标签被 OCR 拆开（如 `发票\\s*代码`）。
    - 抓取标签后的片段，去空格后取最长纯数字串，按长度校验返回。
    """
    m = re.search(label + r"\s*[:：]?\s*([0-9A-Za-z][0-9A-Za-z\s]{%d,%d})" % (minlen - 1, maxlen + 8), text)
    if not m:
        return ""
    clean = re.sub(r"\s+", "", m.group(1))          # OCR 常把长数字读成 '1234 5678'
    clean = re.sub(r"[^0-9A-Za-z]", "", clean)
    runs = re.findall(r"\d+", clean)                # 代码/号码均为纯数字
    if not runs:
        return ""
    best = max(runs, key=len)
    if minlen <= len(best) <= maxlen:
        return best
    for r in runs:                                  # 退回：任取一段符合长度的
        if minlen <= len(r) <= maxlen:
            return r
    return ""


def _parse_text(text):
    res = {"invoice_no": "", "invoice_code": "", "invoice_date": "",
           "untax_amount": 0.0, "tax_amount": 0.0, "total_amount": 0.0, "tax_rate": 0.0}
    # 发票代码（通常 10/12 位，容忍 OCR 空格与标签错位）
    c = _digits_after_label(text, r"发票\s*代码", 8, 20)
    if c:
        res["invoice_code"] = c
    # 发票号码（通常 8 位，容忍空格）
    n = _digits_after_label(text, r"发票\s*号码", 6, 20)
    if n:
        res["invoice_no"] = n
    # 兜底：标签被严重割裂时，整文扫描候选数字串
    if not res["invoice_code"]:
        runs = [r for r in re.findall(r"\d{10,12}", text) if not (len(r) == 8 and r[:2] in ("19", "20"))]
        if runs:
            res["invoice_code"] = max(runs, key=len)[:12]
    if not res["invoice_no"]:
        runs = [r for r in re.findall(r"\d{6,12}", text) if r[:2] not in ("19", "20") and r != res["invoice_code"]]
        if runs:
            res["invoice_no"] = runs[0]
    m = re.search(r"(\d{4})[年\-/](\d{1,2})[月\-/](\d{1,2})", text)
    if m:
        res["invoice_date"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"税率[：:\s]*([\d.]+)\s*%?", text)
    if m:
        try:
            res["tax_rate"] = float(m.group(1))
        except Exception:
            pass
    # 金额（带千分位逗号需去除）
    def _num(p):
        p = p.replace(",", "").replace("，", "")
        try:
            return float(p)
        except Exception:
            return 0.0
    m = re.search(r"价税合计[（(]?[^\n]*?[：:\s]*[¥￥]?\s*([0-9,]+\.?\d*)", text)
    if m:
        res["total_amount"] = _num(m.group(1))
    m = re.search(r"税额[：:\s]*[¥￥]?\s*([0-9,]+\.?\d*)", text)
    if m:
        res["tax_amount"] = _num(m.group(1))
    m = re.search(r"(?<!价税)金额[：:\s]*[¥￥]?\s*([0-9,]+\.?\d*)", text)
    if m:
        res["untax_amount"] = _num(m.group(1))
    return res


# ───────────────────────────────────────────────────────────
# 主入口
# ───────────────────────────────────────────────────────────
def _recognize_img(img):
    """对一张 cv2 图像做发票识别，返回结构化字段 + 原始串 + 使用的引擎。"""
    res = {"invoice_no": "", "invoice_code": "", "invoice_date": "",
           "untax_amount": 0.0, "tax_amount": 0.0, "total_amount": 0.0, "tax_rate": 0.0,
           "raw": "", "engine": "", "has_qr": False}
    engine = []
    qr_raw = ""
    if img is not None:
        qr_raw = _decode_qr(img)
    if qr_raw:
        engine.append("qr")
        res["has_qr"] = True
        res.update(_parse_qr(qr_raw))
        res["raw"] = qr_raw

    text = ""
    if TESS_CMD and img is not None:
        text = _ocr_text(img)
    if text:
        engine.append("ocr")
        t = _parse_text(text)
        for k in ("invoice_no", "invoice_code", "invoice_date", "tax_rate"):
            if not res.get(k) and t.get(k):
                res[k] = t[k]
        if not res["total_amount"] and t["total_amount"]:
            res["total_amount"] = t["total_amount"]
        if not res["tax_amount"] and t["tax_amount"]:
            res["tax_amount"] = t["tax_amount"]
        if not res["untax_amount"] and t["untax_amount"]:
            res["untax_amount"] = t["untax_amount"]
        if not res["raw"]:
            res["raw"] = text[:500]

    res["engine"] = "+".join(engine) if engine else "none"
    for k in ("untax_amount", "tax_amount", "total_amount", "tax_rate"):
        try:
            res[k] = float(res[k] or 0)
        except Exception:
            res[k] = 0.0
    return res


def _score_res(res):
    """给单页识别结果打分，用于多页 PDF 择优（二维码优先、字段越全越高）。"""
    s = 0
    for k in ("invoice_no", "invoice_code", "invoice_date"):
        if res.get(k):
            s += 2
    for k in ("untax_amount", "tax_amount", "total_amount"):
        if res.get(k):
            s += 1
    if res.get("has_qr"):
        s += 5
    return s


def recognize_invoice_image(rel_path):
    """对保存在数据目录下的发票图片做识别，返回结构化字段 + 原始串 + 使用的引擎。"""
    full = os.path.normpath(os.path.join(get_data_dir(), rel_path))
    base = os.path.normpath(get_data_dir())
    if not (full == base or full.startswith(base + os.sep)):
        raise ValueError("非法图片路径")
    if not os.path.exists(full):
        raise FileNotFoundError("图片文件不存在")
    img = None
    if HAVE_CV2:
        try:
            img = cv2.imread(full)
        except Exception:
            img = None
    if img is None:
        return {"invoice_no": "", "invoice_code": "", "invoice_date": "",
                "untax_amount": 0.0, "tax_amount": 0.0, "total_amount": 0.0, "tax_rate": 0.0,
                "raw": "", "engine": "none", "has_qr": False}
    return _recognize_img(img)


def recognize_invoice_pdf(rel_path):
    """对保存在数据目录下的发票 PDF 做识别：逐页渲染为图片后走图片识别管线，择优返回。"""
    full = os.path.normpath(os.path.join(get_data_dir(), rel_path))
    base = os.path.normpath(get_data_dir())
    if not (full == base or full.startswith(base + os.sep)):
        raise ValueError("非法PDF路径")
    if not os.path.exists(full):
        raise FileNotFoundError("PDF文件不存在")
    if not HAVE_FITZ:
        return {"invoice_no": "", "invoice_code": "", "invoice_date": "",
                "untax_amount": 0.0, "tax_amount": 0.0, "total_amount": 0.0, "tax_rate": 0.0,
                "raw": "未安装 PyMuPDF，无法解析 PDF", "engine": "none", "has_qr": False}
    best = None
    best_score = -1
    try:
        doc = fitz.open(full)
        for page in doc:
            # 2 倍缩放提升小字/二维码识别率
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            img = None
            if HAVE_CV2:
                arr = np.frombuffer(pix.tobytes("png"), dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            r = _recognize_img(img) if img is not None else None
            if r is None:
                continue
            sc = _score_res(r)
            if sc > best_score:
                best_score = sc
                best = r
        doc.close()
    except Exception as e:
        return {"invoice_no": "", "invoice_code": "", "invoice_date": "",
                "untax_amount": 0.0, "tax_amount": 0.0, "total_amount": 0.0, "tax_rate": 0.0,
                "raw": "PDF 解析失败：" + str(e), "engine": "none", "has_qr": False}
    if best is None:
        return {"invoice_no": "", "invoice_code": "", "invoice_date": "",
                "untax_amount": 0.0, "tax_amount": 0.0, "total_amount": 0.0, "tax_rate": 0.0,
                "raw": "PDF 无可用页面", "engine": "none", "has_qr": False}
    return best


def recognize_invoice_file(rel_path):
    """按扩展名自动分发：PDF 走 PDF 管线，其余按图片处理。"""
    ext = os.path.splitext(rel_path or "")[1].lower()
    if ext in ALLOWED_PDF_EXT:
        return recognize_invoice_pdf(rel_path)
    return recognize_invoice_image(rel_path)
