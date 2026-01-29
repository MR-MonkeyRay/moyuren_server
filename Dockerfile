# ============================================
# 多阶段构建 - 依赖安装阶段
# ============================================
FROM python:3.12-slim AS builder

WORKDIR /app

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖到虚拟环境
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ============================================
# 运行阶段
# ============================================
FROM python:3.12-slim

# 支持自定义 UID/GID 以匹配宿主机用户（解决 bind mount 权限问题）
ARG UID=1000
ARG GID=1000

# 创建非 root 用户（使用指定的 UID/GID，兼容已存在的 GID）
RUN (getent group ${GID} || groupadd -g ${GID} appuser) \
    && useradd -u ${UID} -g ${GID} -m appuser

# 安装运行时依赖
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        wget \
        ca-certificates \
        procps \
        tzdata \
    && ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 从 builder 阶段复制虚拟环境
COPY --from=builder /opt/venv /opt/venv

# 设置环境变量
ENV PATH="/opt/venv/bin:$PATH"
ENV PLAYWRIGHT_BROWSERS_PATH=/app/.playwright
ENV TZ=Asia/Shanghai

# 安装 Playwright 浏览器及其系统依赖，并清理 apt 缓存
RUN playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

# 复制启动脚本
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# 复制应用代码
COPY . .

# 创建必要的目录并设置权限
RUN mkdir -p static state logs templates \
    && chown -R appuser:appuser /app

# 切换到非 root 用户
USER appuser

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD wget --spider -q http://127.0.0.1:${SERVER_PORT:-8000}/healthz || exit 1

# 启动命令
ENTRYPOINT ["docker-entrypoint.sh"]
