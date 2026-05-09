#!/bin/bash
set -e

echo "[AWD Target] Starting initialization..."

if [ -n "$SSH_PASSWORD" ]; then
    echo "juice:$SSH_PASSWORD" | chpasswd
    echo "[AWD Target] SSH password configured for user 'juice'"
fi

ARENA_DB="/app/arena/arena.db"

if [ ! -f "$ARENA_DB" ]; then
    echo "[AWD Target] Initializing arena database..."
    sqlite3 "$ARENA_DB" < /app/arena/init_arena_db.sql
    chown juice:juice "$ARENA_DB"
    chmod 644 "$ARENA_DB"
fi

echo "[AWD Target] Generating SSH host keys..."
ssh-keygen -A 2>/dev/null

echo "[AWD Target] Starting SSH service..."
/usr/sbin/sshd

echo "[AWD Target] Starting web server..."
cd /app
exec python3 /app/server.py
