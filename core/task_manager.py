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
            
    def get_history(self, task_id: str) -> List[Dict[str, Any]]:
        """Reads back what append_history has been durably persisting all along —
        real evidence found 2026-08-03: this data was being written on every single
        tool call but never once read back anywhere, so a parked-and-resumed task
        always restarted from a totally blank slate (a fresh task_id via
        process_text -> create_task, `all_results = []`), discarding everything it
        had already genuinely done before pausing. Returns [] for an unknown id
        rather than raising, matching append_history's own no-op-on-missing-row
        behavior."""
        conn = get_db()
        row = conn.execute("SELECT history_json FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return []
        return json.loads(row["history_json"])

    def get_pending_tasks(self) -> List[Dict[str, Any]]:
        conn = get_db()
        rows = conn.execute("SELECT * FROM tasks WHERE status = 'pending'").fetchall()
        return [dict(r) for r in rows]

    def park_task(self, task_id: str, slot: str, question: str, node_id: str = None):
        """Parks a task awaiting one missing piece of info (see ask_slot). Only one
        task can be parked at a time by design — enforced here by superseding any
        other still-parked task, not just left to callers/queries to sort out.
        Speaking the "newest wins" notice to the user is still the caller's job.
        `node_id`, when given, is the EXACT task_steps node to resume at via
        _execute_node — this is what makes "continue" resume precisely instead of
        restarting the whole goal (the old resume path re-ran the entire original
        goal string from scratch, trusting the model not to redo finished work)."""
        conn = get_db()
        with conn:
            conn.execute(
                "UPDATE tasks SET status = 'superseded' WHERE status = 'waiting_input' AND id != ?",
                (task_id,)
            )
            conn.execute(
                "UPDATE tasks SET status = 'waiting_input', pending_slot = ?, pending_question = ?, "
                "parked_node_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (slot, question, node_id, task_id)
            )

    def get_parked_task(self) -> Optional[Dict[str, Any]]:
        """The single most-recently-parked task, if any. 'Newest wins' is enforced
        at park time (only one can be waiting_input — parking a new one implicitly
        supersedes any prior one, matching the one-parked-task-at-a-time design)."""
        conn = get_db()
        row = conn.execute(
            "SELECT * FROM tasks WHERE status = 'waiting_input' ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def unpark_task(self, task_id: str, new_status: str = "in_progress"):
        conn = get_db()
        with conn:
            conn.execute(
                "UPDATE tasks SET status = ?, pending_slot = NULL, pending_question = NULL, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_status, task_id)
            )

    # ---------- HTN step-tree methods (see core/voice/controller.py's _execute_node) ----------
    # Every method here is deliberately additive and node-scoped: a node's own
    # result_json is never accumulated with siblings' or ancestors' — that
    # per-goal accumulation (all_results growing across an entire task) is
    # exactly what drove one round's prompt from ~1KB to ~55KB on a real Word
    # task (confirmed live, 2026-07-28). Durable status (not just in-memory) is
    # what makes "never redo or re-speak an already-finished step" a structural
    # guarantee across retries, parks, and process restarts, not just something
    # hoped for from the model's own good behavior.

    def create_root_step(self, task_id: str, description: str) -> str:
        node_id = str(uuid.uuid4())
        conn = get_db()
        with conn:
            conn.execute(
                "INSERT INTO task_steps (id, task_id, parent_id, seq, depth, description) "
                "VALUES (?, ?, NULL, 0, 0, ?)",
                (node_id, task_id, description)
            )
            conn.execute("UPDATE tasks SET root_step_id = ? WHERE id = ?", (node_id, task_id))
        return node_id

    def create_children(self, parent_id: str, descriptions: List[str]) -> List[str]:
        conn = get_db()
        parent = conn.execute("SELECT task_id, depth FROM task_steps WHERE id = ?", (parent_id,)).fetchone()
        if not parent:
            return []
        child_ids = []
        with conn:
            for i, desc in enumerate(descriptions):
                child_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO task_steps (id, task_id, parent_id, seq, depth, description) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (child_id, parent["task_id"], parent_id, i, parent["depth"] + 1, desc)
                )
                child_ids.append(child_id)
        return child_ids

    def get_step(self, node_id: str) -> Optional[Dict[str, Any]]:
        conn = get_db()
        row = conn.execute("SELECT * FROM task_steps WHERE id = ?", (node_id,)).fetchone()
        return dict(row) if row else None

    def get_children(self, parent_id: str) -> List[Dict[str, Any]]:
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM task_steps WHERE parent_id = ? ORDER BY seq", (parent_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def set_step_tool(self, node_id: str, tool_name: str, params: Dict[str, Any]):
        conn = get_db()
        with conn:
            conn.execute(
                "UPDATE task_steps SET tool_name = ?, params_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (tool_name, json.dumps(params), node_id)
            )

    def set_step_result(self, node_id: str, result: Dict[str, Any]):
        conn = get_db()
        with conn:
            conn.execute(
                "UPDATE task_steps SET result_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (json.dumps(result), node_id)
            )

    def update_step_status(self, node_id: str, status: str, error: str = None):
        conn = get_db()
        with conn:
            conn.execute(
                "UPDATE task_steps SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, error, node_id)
            )

    def set_node_type(self, node_id: str, node_type: str):
        conn = get_db()
        with conn:
            conn.execute(
                "UPDATE task_steps SET node_type = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (node_type, node_id)
            )

    def increment_ai_calls(self, node_id: str):
        conn = get_db()
        with conn:
            conn.execute(
                "UPDATE task_steps SET ai_calls = ai_calls + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (node_id,)
            )

    def set_step_pending_question(self, node_id: str, question: str):
        conn = get_db()
        with conn:
            conn.execute(
                "UPDATE task_steps SET status = 'waiting_input', pending_question = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (question, node_id)
            )
