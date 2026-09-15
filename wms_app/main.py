# -*- coding: utf-8 -*-
"""
轻量化项目版进销存管理系统
启动入口 - 双击即运行 / NAS 部署
"""
import sys
import os
import threading
import time
import socket
import webbrowser

# PyInstaller --windowed 打包后无控制台，sys.stdout/stderr 会被置为 None，
# 导致 uvicorn 日志初始化调用 sys.stdout.isatty() 崩溃，这里提前重定向到日志文件
if getattr(sys, 'frozen', False) and (sys.stdout is None or sys.stderr is None):
    _log_path = os.path.join(os.path.dirname(sys.executable), "wms.log")
    _log_file = open(_log_path, "a", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = _log_file
    if sys.stderr is None:
        sys.stderr = _log_file

# ── NAS 部署配置（优先读取环境变量）──────────────────────
# 端口，默认 8899
WMS_PORT = int(os.environ.get("WMS_PORT", "8899"))
# 监听地址：NAS 部署用 0.0.0.0，本地只监听 127.0.0.1
WMS_HOST = os.environ.get("WMS_HOST", "127.0.0.1")
# 数据目录：可挂载 NAS 共享存储
WMS_DATA_DIR = os.environ.get("WMS_DATA_DIR", "")
# 是否自动打开浏览器（NAS 上设为 0）
WMS_OPEN_BROWSER = os.environ.get("WMS_OPEN_BROWSER", "1") == "1"
# 桌面模式：本地回环地址才启用托盘 + 单实例（NAS 用 0.0.0.0 常驻，不加托盘）
IS_DESKTOP = WMS_HOST in ("127.0.0.1", "localhost")
# ──────────────────────────────────────────────────────────


def resource_path(relative_path):
    """获取资源文件路径（兼容PyInstaller打包）"""
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative_path)


# 如果指定了数据目录，覆盖 database.py 的 DB_PATH
if WMS_DATA_DIR:
    os.makedirs(WMS_DATA_DIR, exist_ok=True)
    os.environ["WMS_DB_PATH"] = os.path.join(WMS_DATA_DIR, "wms_data.db")

# 初始化数据库
from database import init_db
init_db()

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from routers_base import router as base_router
from routers_order import router as order_router
from routers_inv import router as inv_router
from routers_finance import router as finance_router
from routers_system import router as system_router
from routers_period import router as period_router
from routers_auth import router as auth_router
from routers_auth import _tokens, get_allowed_modules
from routers_print import router as print_router
from routers_print import ensure_print_tables
from routers_opening import router as opening_router
# 建表并播种默认打印模板（需在打印路由导入之后）
ensure_print_tables()

app = FastAPI(title="进销存管理系统", version="2.4.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 细粒度权限中间件 ──
# 路径前缀 → 模块权限键；非管理员且未授权该模块时，拦截写操作（POST/PUT/DELETE/PATCH）
PATH_MODULE = {
    '/api/goods': 'goods',
    '/api/categories': 'categories',
    '/api/brands': 'brands',
    '/api/units': 'units',
    '/api/partners': 'partners',
    '/api/warehouses': 'warehouses',
    '/api/projects': 'projects',
    '/api/purchase': 'purchase',
    '/api/sale': 'sale',
    '/api/requisition': 'requisition',
    '/api/inventory': 'inventory',
    '/api/check': 'check',
    '/api/bank_accounts': 'bank_accounts',
    '/api/payments': 'payment',
    '/api/reconciliations': 'reconciliation',
    '/api/expenses': 'expense',
    '/api/commissions': 'commission',
    '/api/commission_payments': 'commission',
    '/api/referrers': 'sale',   # 介绍方下拉：销售开单要用，归销售出库模块
    '/api/invoices': 'invoice',
    '/api/opening': 'system',   # 期初建账：菜单归「系统设置」权限
    '/api/system': 'system',
    '/api/print': 'system',
}
MOD_LABEL = {
    'goods': '商品档案', 'categories': '商品分类', 'brands': '品牌管理', 'units': '计量单位',
    'partners': '往来单位', 'warehouses': '仓库管理', 'purchase': '采购入库', 'sale': '销售出库',
    'requisition': '耗材领用', 'inventory': '库存台账', 'alert': '库存预警', 'logs': '库存流水',
    'check': '库存盘点', 'bank_accounts': '银行账户', 'payment': '收付款管理', 'reconciliation': '对账管理',
    'expense': '费用管理', 'invoice': '发票管理', 'projects': '项目台账', 'stats': '数据报表',
    'commission': '介绍提成管理',
    'system': '系统设置',
}

@app.middleware("http")
async def permission_middleware(request: Request, call_next):
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        token = request.headers.get("X-Token", "")
        allowed = get_allowed_modules(token)
        if allowed is not None and allowed != "ALL":
            path = request.url.path
            mod = None
            for pfx, m in PATH_MODULE.items():
                if path.startswith(pfx):
                    mod = m
                    break
            if mod and mod not in allowed:
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=403,
                    content={"detail": f"无权限访问模块：{MOD_LABEL.get(mod, mod)}（请联系管理员）"}
                )
    return await call_next(request)

app.include_router(base_router, prefix="/api")
app.include_router(order_router, prefix="/api")
app.include_router(inv_router, prefix="/api")
app.include_router(finance_router, prefix="/api")
app.include_router(system_router, prefix="/api")
app.include_router(period_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(print_router, prefix="/api")
app.include_router(opening_router, prefix="/api")

# 提供前端页面
static_dir = resource_path("static")


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(static_dir, "index.html"), encoding='utf-8') as f:
        return f.read()


@app.get("/health")
def health():
    return {"status": "ok", "version": "3.0.0", "host": WMS_HOST, "port": WMS_PORT}


# ── PWA 资源（manifest / 图标）：供手机浏览器"添加到主屏幕"安装为 App ──
from fastapi import HTTPException


@app.get("/manifest.webmanifest")
def pwa_manifest():
    return FileResponse(os.path.join(static_dir, "manifest.webmanifest"),
                        media_type="application/manifest+json")


@app.get("/icon/{name}")
def pwa_icon(name: str):
    p = os.path.join(static_dir, "icon", name)
    if not os.path.isfile(p):
        raise HTTPException(status_code=404, detail="icon not found")
    return FileResponse(p)


# ── 手机端启动器（填写服务器地址后进入系统；可"添加到主屏幕"或作为封装 APK 入口）──
@app.get("/launcher")
@app.get("/launcher.html")
def launcher():
    return FileResponse(os.path.join(static_dir, "launcher.html"),
                        media_type="text/html")


# ═══════════════════════════════════════════════════════════
# 桌面模式支持：系统托盘 + 单实例锁
# ═══════════════════════════════════════════════════════════
_server = None  # 全局 server 引用，供托盘退出时置 should_exit


def _open_browser_delayed():
    time.sleep(1.5)
    webbrowser.open(f"http://127.0.0.1:{WMS_PORT}")


def _port_in_use():
    """检测目标端口是否已被本机监听（用于单实例判断）"""
    try:
        with socket.create_connection(("127.0.0.1", WMS_PORT), timeout=0.5):
            return True
    except OSError:
        return False


def _run_tray_loop():
    """
    在子线程中创建隐藏窗口 + 系统托盘图标，并运行消息循环。
    退出菜单会设置 _server.should_exit=True 并销毁窗口，主线程的 server.run() 随之退出。
    依赖 Windows API（ctypes），非 Windows 或非桌面模式不会调用本函数。
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    shell32 = ctypes.windll.shell32

    # ── 常量 ──
    WM_TRAY = 0x0400 + 1
    NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
    NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4
    ID_OPEN, ID_EXIT = 1001, 1002
    MF_STRING = 0x0
    TPM_RIGHTBUTTON = 0x2
    TPM_RETURNCMD = 0x100
    TRAY_ID = 1

    # ── 结构体 ──
    WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_longlong, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    )

    class WNDCLASS(ctypes.Structure):
        _fields_ = [
            ("style", ctypes.c_uint),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HANDLE),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    class NOTIFYICONDATA(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_ulong),
            ("hWnd", wintypes.HWND),
            ("uID", ctypes.c_uint),
            ("uFlags", ctypes.c_uint),
            ("uCallbackMessage", ctypes.c_uint),
            ("hIcon", wintypes.HICON),
            ("szTip", ctypes.c_wchar * 128),
            ("dwState", ctypes.c_ulong),
            ("dwStateMask", ctypes.c_ulong),
            ("szInfo", ctypes.c_wchar * 256),
            ("uTimeout", ctypes.c_ulong),
            ("szInfoTitle", ctypes.c_wchar * 64),
            ("dwInfoFlags", ctypes.c_ulong),
        ]

    def _show_menu(hWnd):
        hMenu = user32.CreatePopupMenu()
        user32.AppendMenuW(hMenu, MF_STRING, ID_OPEN, "打开系统")
        user32.AppendMenuW(hMenu, MF_STRING, ID_EXIT, "退出")
        user32.SetForegroundWindow(hWnd)
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        cmd = user32.TrackPopupMenuEx(
            hMenu, TPM_RIGHTBUTTON | TPM_RETURNCMD, pt.x, pt.y, hWnd, None
        )
        user32.PostMessageW(hWnd, 0, 0, 0)  # WM_NULL：释放鼠标捕获
        user32.DestroyMenu(hMenu)
        if cmd == ID_OPEN:
            webbrowser.open(f"http://127.0.0.1:{WMS_PORT}")
        elif cmd == ID_EXIT:
            # 删除托盘图标
            nid = NOTIFYICONDATA()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
            nid.hWnd = hWnd
            nid.uID = TRAY_ID
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            # 通知主线程服务器退出
            global _server
            if _server is not None:
                _server.should_exit = True
            user32.DestroyWindow(hWnd)
            user32.PostQuitMessage(0)

    def _wnd_proc(hWnd, msg, wParam, lParam):
        if msg == WM_TRAY:
            # 左/右键按下或弹起都弹出菜单
            if lParam in (0x0201, 0x0202, 0x0204, 0x0205):
                _show_menu(hWnd)
            return 0
        return user32.DefWindowProcW(hWnd, msg, wParam, lParam)

    # 必须持久化回调引用，避免被 GC
    _wnd_proc_ref = WNDPROC(_wnd_proc)

    # ── 设置关键 API 签名（64 位句柄不能截断）──
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.CreateWindowExW.argtypes = [
        ctypes.c_uint, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_uint,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
    user32.LoadImageW.argtypes = [
        wintypes.HINSTANCE, wintypes.LPCWSTR, ctypes.c_uint,
        ctypes.c_int, ctypes.c_int, ctypes.c_uint,
    ]
    user32.LoadImageW.restype = wintypes.HICON
    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.AppendMenuW.argtypes = [
        wintypes.HMENU, ctypes.c_uint, ctypes.c_ulonglong, wintypes.LPCWSTR,
    ]
    user32.AppendMenuW.restype = ctypes.c_int
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = ctypes.c_int
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = ctypes.c_int
    user32.TrackPopupMenuEx.argtypes = [
        wintypes.HMENU, ctypes.c_uint, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, ctypes.c_void_p,
    ]
    user32.TrackPopupMenuEx.restype = wintypes.UINT
    user32.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = ctypes.c_int
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.DestroyMenu.argtypes = [wintypes.HMENU]
    user32.DestroyMenu.restype = ctypes.c_int
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = ctypes.c_int
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = ctypes.c_longlong
    user32.Shell_NotifyIconW = shell32.Shell_NotifyIconW
    user32.Shell_NotifyIconW.argtypes = [ctypes.c_uint, ctypes.POINTER(NOTIFYICONDATA)]
    user32.Shell_NotifyIconW.restype = ctypes.c_int

    # ── 注册隐藏窗口类 ──
    wnd_class = WNDCLASS()
    wnd_class.hInstance = kernel32.GetModuleHandleW(0)
    wnd_class.lpszClassName = "WMS_TrayWnd"
    wnd_class.lpfnWndProc = _wnd_proc_ref
    if not user32.RegisterClassW(ctypes.byref(wnd_class)):
        raise RuntimeError("注册窗口类失败")

    hwnd = user32.CreateWindowExW(
        0, "WMS_TrayWnd", "WMS", 0, 0, 0, 0, 0, 0, 0, wnd_class.hInstance, 0
    )
    if not hwnd:
        raise RuntimeError("创建窗口失败")

    # ── 托盘图标（使用系统默认应用图标，避免额外资源文件）──
    hIcon = user32.LoadImageW(
        0, ctypes.cast(32512, wintypes.LPCWSTR), 1, 0, 0, 0x8040
    )
    nid = NOTIFYICONDATA()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
    nid.hWnd = hwnd
    nid.uID = TRAY_ID
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    nid.uCallbackMessage = WM_TRAY
    nid.hIcon = hIcon
    nid.szTip = "进销存管理系统"
    if not user32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
        print("托盘图标创建失败（可能无桌面环境），程序继续在后台运行", file=sys.stderr)
        return

    # ── 消息循环（阻塞，主线程 server.run 在另一线程）──
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) != 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    # 退出：清理托盘图标（若还在）
    user32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))


if __name__ == "__main__":
    # 单实例：桌面模式下若端口已被占用（上次进程残留），直接打开已有实例并退出
    if IS_DESKTOP and _port_in_use():
        print("[单实例] 检测到已有实例在运行，直接打开浏览器", file=sys.stderr)
        webbrowser.open(f"http://127.0.0.1:{WMS_PORT}")
        os._exit(0)

    print("=" * 55)
    print("  轻量化项目版进销存管理系统 v2.6")
    print(f"  访问地址: http://{WMS_HOST}:{WMS_PORT}")
    if WMS_HOST == "0.0.0.0":
        print(f"  局域网访问: http://<本机IP>:{WMS_PORT}")
    if IS_DESKTOP:
        print("  系统已在右下角托盘运行，右键图标可「退出」")
    else:
        print("  关闭此窗口即停止服务")
    print("=" * 55)

    if WMS_OPEN_BROWSER and IS_DESKTOP:
        threading.Thread(target=_open_browser_delayed, daemon=True).start()

    if IS_DESKTOP:
        # 桌面模式：主线程跑 uvicorn 服务器，子线程跑托盘消息循环
        config = uvicorn.Config(app, host=WMS_HOST, port=WMS_PORT, log_level="warning")
        _server = uvicorn.Server(config)
        tray_thread = threading.Thread(target=_run_tray_loop, daemon=True)
        tray_thread.start()
        try:
            _server.run()
        except Exception as e:
            print("服务器异常退出:", e)
        os._exit(0)
    else:
        # NAS / 服务器模式：直接前台运行，常驻
        uvicorn.run(app, host=WMS_HOST, port=WMS_PORT, log_level="warning")
