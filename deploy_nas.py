#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增量把本地 wms_app 改动的源码文件同步到 NAS 并重建容器（不依赖 SFTP 子系统）。
只同步白名单里的少量源码文件，避免全量 948MB 传输触发远端 tar 缓冲/磁盘问题。
用法:
  WMS_NAS_PASS='你的密码' python deploy_nas.py
依赖: paramiko
"""
import os, sys, time, subprocess, paramiko

HOST = "192.168.194.34"
PORT = 20001
USER = "sexsnail"
LOCAL_PARENT = r"E:/WorkBuddy/wms-app"
COMPOSE_DIR = "/share/CACHEDEV1_DATA/container/wms"

# 本次改动的源码文件（相对 LOCAL_PARENT）
# 2026-09-15 介绍方提成功能 + 提成成本口径改为扣点后实付
FILES = [
    "wms_app/database.py",
    "wms_app/main.py",
    "wms_app/routers_order.py",
    "wms_app/routers_finance.py",
    "wms_app/routers_base.py",
    "wms_app/static/index.html",
]

PASS = os.environ.get("WMS_NAS_PASS")
if not PASS:
    print("ERROR: 请通过环境变量 WMS_NAS_PASS 传入 NAS 密码")
    sys.exit(1)


def log(*a):
    print(*a, flush=True)


def upload_files(ssh, local_parent, files, remote_parent):
    """本地 tar 仅打包白名单文件 -> SSH session 通道 -> 远端 tar 解压覆盖"""
    tar = subprocess.Popen(
        ["tar", "czf", "-", "--exclude=__pycache__", "-C", local_parent] + files,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    chan = ssh.get_transport().open_session()
    # 远端 stderr 重定向到文件，避免管道缓冲满导致远端进程 SIGPIPE 死亡
    chan.exec_command("cd %s; rm -rf wms_app/__pycache__ 2>/dev/null; tar xzf - 2>/tmp/tar_err; echo RC=$? >>/tmp/tar_err" % remote_parent)
    sent = 0
    while True:
        chunk = tar.stdout.read(65536)
        if not chunk:
            break
        chan.send(chunk)
        sent += len(chunk)
    tar.stdout.close()
    tar.wait()
    try:
        chan.shutdown_write()
    except Exception:
        pass
    # 消费远端输出，避免通道卡死
    out = chan.makefile("r").read().decode("utf-8", "replace")
    err = chan.makefile_stderr("r").read().decode("utf-8", "replace")
    terr = tar.stderr.read().decode("utf-8", "replace")
    rc = chan.recv_exit_status()
    log("    已发送 %d 字节, 远端 tar rc=%s" % (sent, rc))
    if terr.strip():
        log("    [本地 tar 警告] %s" % terr.strip()[:200])
    if rc != 0 or err.strip():
        # 远端错误已重定向到 /tmp/tar_err，再读一次
        i2, o2, e2 = ssh.exec_command("cat /tmp/tar_err 2>&1")
        log("    [远端 tar_err] %s" % (o2.read().decode().strip()[:400] or err.strip()[:400]))
        return False
    log("    源码文件已覆盖到 %s/wms_app" % remote_parent)
    return True


def run(ssh, cmd, timeout=600):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    return stdout.read().decode("utf-8", "replace"), stderr.read().decode("utf-8", "replace")


def main():
    log("==> 连接 NAS %s:%s@%s" % (USER, PORT, HOST))
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, port=PORT, username=USER, password=PASS, timeout=20)
    ssh.get_transport().set_keepalive(30)

    log("==> 增量上传改动源码 (%d 个文件)" % len(FILES))
    ok = upload_files(ssh, LOCAL_PARENT, FILES, COMPOSE_DIR)
    if not ok:
        log("==> ❌ 上传失败，终止")
        ssh.close()
        return

    log("==> 查找 docker 可执行文件")
    probe = ("for p in /usr/local/bin/docker "
             "/share/CACHEDEV1_DATA/.qpkg/container-station/usr/bin/.libs/docker "
             "/share/CACHEDEV2_DATA/.qpkg/ContainerStation/bin/docker; do "
             "[ -x \"$p\" ] && echo FOUND:$p && break; done; "
             "command -v docker; command -v docker-compose")
    out, err = run(ssh, probe)
    docker = ""
    for line in out.splitlines():
        if line.startswith("FOUND:"):
            docker = line.split(":", 1)[1]
            break
    if not docker:
        docker = (out.strip().splitlines() or ["docker"])[0] or "docker"
    log("    docker = %s" % docker)

    log("==> 执行 docker compose 重建（前台，可能需数分钟）")
    launched = False
    for sub in ("compose", "docker-compose"):
        log("    尝试: %s %s up -d --build" % (docker, sub))
        o, e = run(ssh, "cd %s && %s %s up -d --build" % (COMPOSE_DIR, docker, sub), timeout=590)
        combined = (o + e).strip()
        log("    [输出尾部] %s" % " / ".join(combined.splitlines()[-8:]))
        if "is not a docker command" in combined or "Unknown command" in combined:
            log("    compose 子命令不可用，尝试 docker-compose")
            continue
        launched = True
        break
    if not launched:
        log("    ⚠️ 构建命令未成功，最后输出：")
        log(combined[-600:])
        ssh.close()
        return

    log("==> 等待构建/启动 (最多 9 分钟) ...")
    deadline = time.time() + 540
    health_ok = False
    while time.time() < deadline:
        time.sleep(20)
        out, _ = run(ssh,
            "tail -n 12 /tmp/wms_build.log 2>/dev/null; "
            "echo '---HEALTH---'; "
            "curl -s -o /dev/null -w '%{http_code}' http://localhost:8899/health 2>/dev/null || echo none")
        lines = out.split("---HEALTH---")
        tail = " / ".join([l for l in lines[0].strip().splitlines()][-3:]) if lines[0].strip() else "(空)"
        log("    [build] %s" % tail)
        if len(lines) > 1:
            code = lines[1].strip()
            if code == "200":
                health_ok = True
                break
    if health_ok:
        log("==> ✅ /health 返回 200，部署成功")
        o, _ = run(ssh, "docker ps --filter name=wms-app --format '{{.Names}} {{.Status}}' 2>/dev/null || true")
        log("    容器状态: %s" % o.strip())
    else:
        log("==> ⚠️ 健康检查未通过，最后日志：")
        o, _ = run(ssh, "tail -n 40 /tmp/wms_build.log")
        log(o)
    ssh.close()


if __name__ == "__main__":
    main()
