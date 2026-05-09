-- OpenClaw AWD 竞技场数据库初始化脚本
-- 创建 arena_secret 表用于存放 Flag

-- 创建 Flag 存储表
CREATE TABLE IF NOT EXISTS arena_secret (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flag TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 创建索引以提高查询性能
CREATE INDEX IF NOT EXISTS idx_flag ON arena_secret(flag);
CREATE INDEX IF NOT EXISTS idx_created_at ON arena_secret(created_at);

-- 插入初始 Flag（将在比赛开始时被裁判系统替换）
INSERT OR IGNORE INTO arena_secret (flag) VALUES ('FLAG{initial_placeholder_flag}');

-- 创建触发器：更新 updated_at 字段
CREATE TRIGGER IF NOT EXISTS update_arena_secret_timestamp 
AFTER UPDATE ON arena_secret
BEGIN
    UPDATE arena_secret SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
