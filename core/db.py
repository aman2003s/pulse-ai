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
    conn.close()

if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
