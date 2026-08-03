# 进销存管理系统 — 威联通 NAS 部署说明

## 目录结构

```
20260428104439/           ← 项目根目录
├── docker-compose.yml    ← Compose 配置（放这里）
├── wms_app/              ← 应用源码
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py
│   ├── database.py
│   ├── routers_*.py
│   └── static/
```

---

## 方法一：Container Station 界面部署（推荐）

### 第一步：上传文件到 NAS

**本部署采用「应用与数据分离」布局：**
- 应用/配置（docker-compose.yml + wms_app/）→ 放 **CACHEDEV1**：`/share/CACHEDEV1_DATA/container/wms/`
- 数据库（volumes 挂载的 `/data`）→ 落 **CACHEDEV2**：`/share/CACHEDEV2_DATA/mydata/wms/`

把整个项目目录（`docker-compose.yml` + `wms_app/`）上传到 CACHEDEV1：
```
/share/CACHEDEV1_DATA/container/wms/
```
**注意**：
- 路径前缀 `CACHEDEV1_DATA` / `CACHEDEV2_DATA` 以你 NAS 实际存储池编号为准（File Station 右键文件夹 → 属性查看真实路径）。
- 确保 `wms_app/` 和 `docker-compose.yml` 在同一目录下（都在 CACHEDEV1 那个目录里）。
- 数据库目录 CACHEDEV2 那个路径**不用手建**，Docker 启动时会自动创建（前提是父目录 `/share/CACHEDEV2_DATA/mydata/` 已存在）。

`docker-compose.yml` 已包含 `build:` 段，Container Station「创建应用程序」会**自动用本地 Dockerfile 构建镜像**，
无需提前手动 build（首次构建约 3～10 分钟，取决于网速）。

### 第二步：在 Container Station 中创建应用

1. 打开 **Container Station** → 顶部点「**创建**」
2. 选「**创建应用程序**」
3. 应用名称填：`wms`
4. 把 `docker-compose.yml` 内容**粘贴进编辑框**
5. 确认 volumes 路径指向 **CACHEDEV2 的数据库目录**（与 CACHEDEV1 上的项目目录分开，这是预期的分离布局）：
   ```yaml
   volumes:
     - /share/CACHEDEV2_DATA/mydata/wms:/data
   ```
6. 点击「**验证**」→「**创建**」（会自动构建镜像并启动）

### 第三步：等待构建完成

首次构建需要下载 Python 镜像和安装依赖，约需 3～10 分钟（取决于网速）。

---

## 方法二：SSH 命令行部署

```bash
# 登录 NAS（SSH）
ssh admin@<NAS_IP>

# ⚠️ 关键：Container Station 的 docker 不在默认 PATH，先把它加进来
# 路径前缀 CACHEDEV2_DATA 按你实际存储池修改（如不确定，用下面命令找）：
#   find /share -name docker -type f 2>/dev/null
export PATH=$PATH:/share/CACHEDEV2_DATA/.qpkg/ContainerStation/bin

# 进入项目目录（应用配置在 CACHEDEV1；数据库在 CACHEDEV2 由 volumes 决定）
cd /share/CACHEDEV1_DATA/container/wms

# ⚠️ 关键：新版 Container Station 用 Compose V2，命令是「docker compose」（空格，不是连字符）
# 构建并启动
docker compose up -d --build

# 查看日志
docker compose logs -f wms

# 停止
docker compose down
```

---

## 访问系统

启动成功后，局域网内任意设备浏览器访问：
```
http://<NAS的IP>:8899
```

默认账号：**admin / admin123**（首次登录后请在用户管理中修改密码）

---

## 数据备份

数据库文件位于 NAS 本地：
```
/share/CACHEDEV2_DATA/mydata/wms/wms_data.db
```

直接复制该文件即可备份。也可以在系统内使用「备份与导入导出」功能。

---

## 修改端口

如果 8899 端口被占用，在 `docker-compose.yml` 中修改：
```yaml
ports:
  - "9000:8899"   # 改为 9000 对外访问
```
访问地址变为 `http://<NAS_IP>:9000`，容器内部端口 8899 不变。

---

## 常见问题

**Q：构建失败，pip 下载超时**
修改 Dockerfile 中的 pip 源，或使用阿里云源：
```
-i https://mirrors.aliyun.com/pypi/simple/
```

**Q：数据库文件权限问题**
```bash
chmod 755 /share/CACHEDEV2_DATA/mydata/wms
```

**Q：容器正常但浏览器访问不了**
检查威联通防火墙是否开放了 8899 端口：
控制台 → 安全 → 防火墙 → 添加规则，允许 TCP 8899。
