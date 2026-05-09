#!/bin/bash
set -e

echo "[AWD Target Test] Starting initialization..."

if [ -n "$SSH_PASSWORD" ]; then
    echo "juice:$SSH_PASSWORD" | chpasswd
    echo "[AWD Target Test] SSH password configured for user 'juice'"
else
    echo "[AWD Target Test] WARNING: SSH_PASSWORD not set, using default password"
fi

ARENA_DB="/app/arena/arena.db"

if [ ! -f "$ARENA_DB" ]; then
    echo "[AWD Target Test] Initializing arena database..."
    sqlite3 "$ARENA_DB" < /app/arena/init_arena_db.sql
    chown juice:juice "$ARENA_DB"
    chmod 644 "$ARENA_DB"
    echo "[AWD Target Test] Arena database initialized at $ARENA_DB"
else
    echo "[AWD Target Test] Arena database already exists"
fi

echo "[AWD Target Test] Generating SSH host keys..."
ssh-keygen -A

echo "[AWD Target Test] Starting SSH service..."
/usr/sbin/sshd

echo "=========================================="
echo "OpenClaw AWD Target Test Configuration"
echo "=========================================="
echo "HTTP Port:        3000"
echo "SSH Port:         22"
echo "SSH User:         juice"
echo "Arena Database:   $ARENA_DB"
echo "=========================================="

echo "[AWD Target Test] Starting HTTP server..."
cd /app
python3 -m http.server 3000
