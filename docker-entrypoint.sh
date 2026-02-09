#!/bin/sh
set -e

PORT=${SERVER__PORT:-${SERVER_PORT:-8000}}
HOST=${SERVER__HOST:-${SERVER_HOST:-0.0.0.0}}

# 以 root 启动时：初始化目录权限后降权到 appuser
if [ "$(id -u)" = "0" ]; then
    # 确保挂载目录存在且权限正确
    mkdir -p /app/static /app/state
    chown -R appuser:appuser /app/static /app/state

    # 如果传入了自定义命令，用 gosu 降权执行
    if [ $# -gt 0 ]; then
        exec gosu appuser "$@"
    fi

    echo "Starting Moyuren API on ${HOST}:${PORT}..."
    exec gosu appuser uvicorn app.main:app --host "${HOST}" --port "${PORT}"
fi

# 非 root 直接运行（兼容已正确设置权限的场景）
if [ $# -gt 0 ]; then
    exec "$@"
fi

echo "Starting Moyuren API on ${HOST}:${PORT}..."
exec uvicorn app.main:app --host "${HOST}" --port "${PORT}"
