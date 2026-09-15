import os, hashlib, paramiko, sys

NAS_HOST = '192.168.194.34'
NAS_PORT = 20001
NAS_USER = 'sexsnail'
PASS = os.environ.get('WMS_NAS_PASS')
if not PASS:
    print('need env WMS_NAS_PASS'); sys.exit(1)

LOCAL_BASE = r'e:/WorkBuddy/2026-04-28-10-44-39/wms_app'
REMOTE_BASE = '/share/CACHEDEV1_DATA/container/wms/wms_app'
DOCKER = '/share/CACHEDEV1_DATA/.qpkg/container-station/usr/bin/.libs/docker'

FILES = ['database.py', 'routers_opening.py', 'main.py', 'routers_finance.py', 'static/index.html']


def md5(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(8192), b''):
            h.update(b)
    return h.hexdigest()


def run(ssh, cmd):
    i, o, e = ssh.exec_command(cmd)
    return o.read().decode(), e.read().decode()


ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(NAS_HOST, port=NAS_PORT, username=NAS_USER, password=PASS, timeout=20)

print('=== 本地 md5 ===')
local_md5 = {}
for f in FILES:
    p = os.path.join(LOCAL_BASE, f)
    if os.path.exists(p):
        local_md5[f] = md5(p)
        print(f, local_md5[f])
    else:
        print('本地缺失', p)

print('=== 远端 md5 ===')
remote_md5 = {}
for f in FILES:
    rp = f'{REMOTE_BASE}/{f}'
    out, err = run(ssh, f'md5sum {rp}')
    if out.strip():
        remote_md5[f] = out.split()[0]
        print(f, remote_md5[f])
    else:
        print('远端缺失', rp, err.strip()[:80])

print('=== 比对 ===')
allok = True
for f in FILES:
    l, r = local_md5.get(f), remote_md5.get(f)
    if l and r:
        ok = l == r
        allok = allok and ok
        print(f, 'OK' if ok else 'MISMATCH 不一致!')
    else:
        allok = False
        print(f, 'CHECK FAILED')

print('=== 容器状态 ===')
out, err = run(ssh, f'{DOCKER} ps --filter name=wms-app --format "{{{{.Names}}}} {{{{.Status}}}}"')
print(out.strip() or err.strip() or '(无 wms-app 容器)')

print('=== health ===')
out, err = run(ssh, 'curl -s -m 5 http://127.0.0.1:8899/health; echo')
print(repr(out.strip()) or err.strip() or '(health 无响应)')

print('=== 容器日志(末40行) ===')
out, err = run(ssh, f'{DOCKER} logs --tail 40 wms-app')
print(out.strip() or err.strip() or '(无日志)')

ssh.close()
print('=== 结论 ===', '文件全部一致' if allok else '存在不一致/缺失，需复查')
