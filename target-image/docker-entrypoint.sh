#!/bin/bash
set -e

echo "[AWD Target] Starting initialization..."

# 1. 配置 SSH 密码（从环境变量读取）
if [ -n "$SSH_PASSWORD" ]; then
    echo "juice:$SSH_PASSWORD" | chpasswd
    echo "[AWD Target] SSH password configured for user 'juice'"
else
    echo "[AWD Target] WARNING: SSH_PASSWORD not set, using default password"
fi

# 2. 初始化 Arena 数据库
ARENA_DB="/app/arena/arena.db"

if [ ! -f "$ARENA_DB" ]; then
    echo "[AWD Target] Initializing arena database..."
    sqlite3 "$ARENA_DB" < /app/arena/init_arena_db.sql
    chown juice:juice "$ARENA_DB"
    chmod 644 "$ARENA_DB"
    echo "[AWD Target] Arena database initialized at $ARENA_DB"
else
    echo "[AWD Target] Arena database already exists"
fi

# 3. 启动 SSH 服务
echo "[AWD Target] Starting SSH service..."
service ssh start

# 4. 打印启动信息
echo "=========================================="
echo "OpenClaw AWD Target Configuration"
echo "=========================================="
echo "Juice Shop Port:  3000"
echo "SSH Port:         22"
echo "SSH User:         juice"
echo "Arena Database:   $ARENA_DB"
echo "=========================================="

# 5. 启动 Juice Shop（使用原始 entrypoint）
echo "[AWD Target] Starting Juice Shop..."
exec npm start
