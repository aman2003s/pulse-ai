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

-- HTN-style recursive step tree (self-referential): a goal is the root node,
-- decomposed into children of arbitrary depth. tasks.plan_json/current_step
-- (a flat array + integer index) can't represent this nesting, so this is a
-- separate table rather than a repurposing of those columns.
CREATE TABLE IF NOT EXISTS task_steps (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    parent_id TEXT,
    seq INTEGER NOT NULL,
    depth INTEGER NOT NULL DEFAULT 0,
    description TEXT NOT NULL,
    node_type TEXT NOT NULL DEFAULT 'unknown',
    status TEXT NOT NULL DEFAULT 'pending',
    tool_name TEXT,
    params_json TEXT,
    result_json TEXT,
    ai_calls INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    pending_question TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    FOREIGN KEY (parent_id) REFERENCES task_steps(id)
);
CREATE INDEX IF NOT EXISTS idx_task_steps_parent ON task_steps(parent_id, seq);
CREATE INDEX IF NOT EXISTS idx_task_steps_task ON task_steps(task_id);
