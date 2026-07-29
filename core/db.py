import sqlite3
import os

DB_PATH = os.path.expandvars(r"%APPDATA%\Pulse\pulse.db")

def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
    with open(schema_path, 'r', encoding='utf-8') as f:
        schema = f.read()
    conn = get_db()
    with conn:
        conn.executescript(schema)
    migrate_db(conn)
    conn.close()

def migrate_db(conn=None):
    """Additive, idempotent column migrations for DBs created before a column
    existed — CREATE TABLE IF NOT EXISTS in schema.sql only helps brand-new DBs;
    an existing tasks table needs ALTER TABLE to actually gain new columns.
    Safe to call on every startup: SQLite has no 'ADD COLUMN IF NOT EXISTS',
    so a "duplicate column" failure here is the normal, expected case."""
    own_conn = conn is None
    conn = conn or get_db()
    for stmt in (
        "ALTER TABLE tasks ADD COLUMN pending_slot TEXT",
        "ALTER TABLE tasks ADD COLUMN pending_question TEXT",
        # root_step_id: the task_steps row that's this task's tree root.
        # parked_node_id: the EXACT node a parked task resumes at — this is what
        # lets "continue" resume precisely instead of restarting the whole goal
        # (see core/voice/controller.py's _execute_node/process_text).
        "ALTER TABLE tasks ADD COLUMN root_step_id TEXT",
        "ALTER TABLE tasks ADD COLUMN parked_node_id TEXT",
    ):
        try:
            with conn:
                conn.execute(stmt)
        except Exception:
            pass
    if own_conn:
        conn.close()

if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
