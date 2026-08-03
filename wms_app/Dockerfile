# ── 进销存管理系统 Docker 镜像 ──────────────────────────────
# 基础镜像：轻量 Python 3.12
# ⚠️ 国内 NAS 直连 Docker Hub 常超时（报 530 / Client.Timeout），改用国内镜像源拉取。
#   若以下中科大源拉取失败，可替换为：
#     阿里云  registry.cn-hangzhou.aliyuncs.com/library/python:3.12-slim
#     网易    hub-mirror.c.163.com/library/python:3.12-slim
#   原官方镜像名（网络可达时）：python:3.12-slim
FROM docker.mirrors.ustc.edu.cn/library/python:3.12-slim

# 设置工作目录
WORKDIR /app

# 安装依赖（先复制 requirements，利用镜像层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    --trusted-host pypi.tuna.tsinghua.edu.cn

# 复制项目文件（排除 .dockerignore 中的内容）
COPY . .

# 创建数据目录
RUN mkdir -p /data

# 暴露端口
EXPOSE 8899

# 环境变量默认值（可在 docker-compose.yml 中覆盖）
ENV WMS_HOST=0.0.0.0 \
    WMS_PORT=8899 \
    WMS_DATA_DIR=/data \
    WMS_OPEN_BROWSER=0

# 启动命令
CMD ["python", "main.py"]
