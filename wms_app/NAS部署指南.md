# 进销存管理系统 - NAS 部署指南

## 一、部署方式总览

| 方式 | 适用场景 | 难度 |
|------|---------|------|
| **直接运行 EXE**（Windows 电脑当服务器） | 家用 NAS、办公电脑 24h 开机 | ⭐ 最简单 |
| **Docker 容器**（群晖、威联通、QNAP、开放平台） | 主流 NAS 设备 | ⭐⭐ |
| **Python 直接运行**（Linux / OpenWRT / 路由器） | 极客用户 | ⭐⭐ |

---

## 二、方式 A：Windows 电脑作为服务器（最简单）

### 步骤
1. 将 `进销存管理系统.exe` 复制到服务器电脑任意目录
2. 双击运行，控制台窗口显示 `访问地址: http://127.0.0.1:8899`
3. **让局域网其他人访问**：
   - 方法：用环境变量改监听地址（以管理员权限打开 PowerShell）：
     ```powershell
     $env:WMS_HOST = "0.0.0.0"
     $env:WMS_PORT = "8899"
     $env:WMS_OPEN_BROWSER = "0"
     & "C:\path\to\进销存管理系统.exe"
     ```
   - 或者创建批处理文件 `start_server.bat`：
     ```bat
     @echo off
     set WMS_HOST=0.0.0.0
     set WMS_PORT=8899
     set WMS_OPEN_BROWSER=0
     进销存管理系统.exe
     ```
4. 其他电脑访问：`http://服务器IP:8899`
5. **开机自启**：将 `start_server.bat` 快捷方式放入"启动"文件夹（Win+R → `shell:startup`）

### 数据目录自定义（将数据放到共享盘）
```bat
set WMS_DATA_DIR=D:\共享数据\WMS
```

### 防火墙放行
```powershell
New-NetFirewallRule -DisplayName "WMS端口8899" -Direction Inbound -LocalPort 8899 -Protocol TCP -Action Allow
```

---

## 三、方式 B：Docker 容器部署（推荐 NAS 用户）

### 前提条件
- 群晖 DSM 6.2+ / 威联通 QTS / 其他支持 Docker 的 NAS
- 已安装 Docker 或 Container Manager

### 群晖 DSM 操作步骤

#### 1. 上传应用文件
通过 File Station 将应用目录（含 `main.py`、`static/` 等）上传到：
```
/volume1/docker/wms/
```

#### 2. 使用 docker-compose（推荐）
SSH 进入群晖，执行：
```bash
cd /volume1/docker/wms
docker-compose up -d
```

#### 3. 使用 Container Manager 图形界面
1. 打开 **Container Manager → 项目 → 新增**
2. 选择路径 `/volume1/docker/wms`，加载 `docker-compose.yml`
3. 点击"部署"

#### 4. 访问系统
```
http://群晖IP:8899
```

### 数据持久化
数据库文件保存在 Docker 卷 `wms_data` 中。  
若要指定到 NAS 共享目录（如 `/volume1/shared/wms_data`），修改 `docker-compose.yml`：
```yaml
volumes:
  - /volume1/shared/wms_data:/app/data
```

### 威联通 QNAP 操作步骤
1. 安装 **Container Station**
2. 创建应用，上传 `docker-compose.yml`
3. 数据目录改为：`/share/Container/wms_data:/app/data`
4. 部署并启动

---

## 四、方式 C：Python 直接运行（Linux 服务器/VPS）

### 环境要求
- Python 3.9+
- pip

### 安装步骤
```bash
# 1. 上传文件
scp -r wms_app/ user@nas-ip:/opt/wms/

# 2. 安装依赖
cd /opt/wms
pip3 install fastapi uvicorn[standard] pydantic

# 3. 配置环境变量
export WMS_HOST=0.0.0.0
export WMS_PORT=8899
export WMS_DATA_DIR=/opt/wms/data
export WMS_OPEN_BROWSER=0

# 4. 启动
python3 main.py
```

### 后台常驻（systemd 服务）
创建 `/etc/systemd/system/wms.service`：
```ini
[Unit]
Description=进销存管理系统
After=network.target

[Service]
Type=simple
User=nobody
WorkingDirectory=/opt/wms
Environment=WMS_HOST=0.0.0.0
Environment=WMS_PORT=8899
Environment=WMS_DATA_DIR=/opt/wms/data
Environment=WMS_OPEN_BROWSER=0
ExecStart=/usr/bin/python3 /opt/wms/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

启用服务：
```bash
systemctl daemon-reload
systemctl enable wms
systemctl start wms
systemctl status wms
```

---

## 五、环境变量说明

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `WMS_HOST` | `127.0.0.1` | 监听地址，NAS 部署改为 `0.0.0.0` |
| `WMS_PORT` | `8899` | 服务端口 |
| `WMS_DATA_DIR` | （空，程序目录） | 数据库存储目录，建议 NAS 部署设置 |
| `WMS_OPEN_BROWSER` | `1` | 启动时自动打开浏览器，NAS 设 `0` |

---

## 六、反向代理（可选，通过域名/HTTPS 访问）

### 群晖内置反向代理
**控制面板 → 应用程序门户 → 反向代理**，新增：
- 来源：`https://wms.yourdomain.com:443`
- 目标：`http://localhost:8899`

### Nginx 配置
```nginx
server {
    listen 80;
    server_name wms.local;

    location / {
        proxy_pass http://127.0.0.1:8899;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_http_version 1.1;
        proxy_read_timeout 300;
    }
}
```

---

## 七、数据备份

数据库文件为 `wms_data.db`（SQLite），直接复制该文件即为完整备份。

### 自动备份（Linux）
```bash
# 每天凌晨 2 点备份
0 2 * * * cp /opt/wms/data/wms_data.db /backup/wms_$(date +%Y%m%d).db
```

---

## 八、常见问题

| 问题 | 解决方案 |
|------|---------|
| 局域网无法访问 | 确认 `WMS_HOST=0.0.0.0`，检查防火墙是否放行 8899 端口 |
| 端口被占用 | 修改 `WMS_PORT` 为其他端口，如 8080 |
| 数据丢失 | 检查 `WMS_DATA_DIR` 是否指向持久化路径 |
| 启动报错 | 检查 Python 版本（需 3.9+）及依赖是否安装 |
| 速度慢 | SQLite 不适合 100+ 并发，如需高并发请联系升级版本 |
