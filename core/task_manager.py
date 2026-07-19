import json
import uuid
from typing import Dict, Any, List, Optional
from core.db import get_db
import logging

logger = logging.getLogger(__name__)

class TaskManager:
    def __init__(self):
        pass
        
    def create_task(self, goal: str) -> str:
        task_id = str(uuid.uuid4())
        conn = get_db()
        with conn:
            conn.execute(
                "INSERT INTO tasks (id, goal, status, current_step, plan_json, history_json) VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, goal, "pending", 0, "[]", "[]")
            )
        return task_id
        
    def update_task_status(self, task_id: str, status: str, result: str = None):
        conn = get_db()
        with conn:
            if result is not None:
                conn.execute(
                    "UPDATE tasks SET status = ?, result = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (status, result, task_id)
                )
            else:
                conn.execute(
                    "UPDATE tasks SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (status, task_id)
                )
                
    def append_history(self, task_id: str, event: Dict[str, Any]):
        conn = get_db()
        row = conn.execute("SELECT history_json FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return
            
        history = json.loads(row["history_json"])
        history.append(event)
        
        with conn:
            conn.execute(
                "UPDATE tasks SET history_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (json.dumps(history), task_id)
            )
            
    def get_pending_tasks(self) -> List[Dict[str, Any]]:
        conn = get_db()
        rows = conn.execute("SELECT * FROM tasks WHERE status = 'pending'").fetchall()
        return [dict(r) for r in rows]
