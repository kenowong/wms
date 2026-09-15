# -*- coding: utf-8 -*-
"""启动页与静态资源路由冒烟测试（/launcher、/launcher.html、/、manifest、icon）"""
import os
import sys
import shutil
import time
import threading
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, 'wms_app')
sys.path.insert(0, APP)

DBDIR = os.path.join(HERE, '_test_data_smoke')
if os.path.exists(DBDIR):
    shutil.rmtree(DBDIR)
os.makedirs(DBDIR)
os.environ['WMS_DB_PATH'] = os.path.join(DBDIR, 'test.db')
os.environ['WMS_OPEN_BROWSER'] = '0'
PORT = 8977
import uvicorn
from main import app  # noqa: E402

BASE = f'http://127.0.0.1:{PORT}'
PASS = [0]
FAIL = [0]


def check(name, cond, extra=''):
    if cond:
        PASS[0] += 1
        print(f'  PASS  {name}')
    else:
        FAIL[0] += 1
        print(f'  FAIL  {name}  {extra}')


def get(path):
    try:
        r = urllib.request.urlopen(BASE + path, timeout=10)
        return r.status, r.read().decode('utf-8', 'ignore')
    except urllib.error.HTTPError as e:
        return e.code, ''
    except Exception as e:
        return -1, str(e)


def run_server():
    uvicorn.run(app, host='127.0.0.1', port=PORT, log_level='error')


t = threading.Thread(target=run_server, daemon=True)
t.start()
for _ in range(60):
    time.sleep(0.3)
    s, _b = get('/health')
    if s == 200:
        break

print('=== 静态与启动页路由冒烟测试 ===')
s, body = get('/health')
check('/health = 200', s == 200, f'got {s}')

s, body = get('/launcher')
check('/launcher = 200', s == 200, f'got {s}')
check('/launcher 返回启动页内容(含 连接/服务器 字样)',
      ('连接' in body) or ('服务器' in body), body[:80].replace('\n', ' '))

s, body2 = get('/launcher.html')
check('/launcher.html = 200', s == 200, f'got {s}')
check('/launcher.html 与 /launcher 内容一致', body == body2)

s, idx = get('/')
check('/ = 200 且为系统主页', s == 200 and ('进销存' in idx or 'app' in idx or len(idx) > 10000),
      f'got {s} len={len(idx)}')

s, mf = get('/manifest.webmanifest')
check('/manifest.webmanifest 可访问', s == 200, f'got {s}')

s, ic = get('/icon/icon-192.png')
check('/icon/icon-192.png 可访问(或 404 非关键)', s in (200, 404), f'got {s}')

s, op = get('/api/opening/debt')
check('期初接口已挂载(401/403 未登录、405 方法不许 都说明路由存在)', s in (200, 401, 403, 405), f'got {s}')

print()
print(f'=== 结果：{PASS[0]} 项 PASS，{FAIL[0]} 项 FAIL ===')
sys.exit(1 if FAIL[0] else 0)
