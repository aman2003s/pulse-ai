CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    goal TEXT,
    status TEXT,
    current_step INTEGER,
    plan_json TEXT,
    history_json TEXT,
    result TEXT,
    pending_slot TEXT,
    pending_question TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS app_index (
    name TEXT PRIMARY KEY,
    path TEXT,
    aliases TEXT
);

CREATE TABLE IF NOT EXISTS file_index (
    path TEXT PRIMARY KEY,
    name TEXT,
    mtime REAL
);
