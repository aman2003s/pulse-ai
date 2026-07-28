import time
from typing import List, Dict, Any

class ConversationManager:
    def __init__(self, max_history=5, idle_timeout_s=120):
        self.history = []
        self.max_history = max_history
        self.idle_timeout_s = idle_timeout_s
        self.last_interaction_time = time.time()
        
    def add_exchange(self, user_text: str, assistant_plan: Dict[str, Any], results: List[Dict[str, Any]]):
        now = time.time()
        if now - self.last_interaction_time > self.idle_timeout_s:
            self.history = []
            
        self.history.append({
            "user": user_text,
            "assistant": assistant_plan,
            "results": results
        })
        
        if len(self.history) > self.max_history:
            self.history.pop(0)
            
        self.last_interaction_time = now
        
    def get_context_string(self) -> str:
        now = time.time()
        if now - self.last_interaction_time > self.idle_timeout_s:
            self.history = []

        if not self.history:
            return ""

        # Deliberately NOT including what Pulse previously SAID (only the user's
        # request and the real tool outcomes) — confirmed real bug: the old
        # "Pulse (spoken)" line was the ONLY thing ever actually injected here
        # (the "Pulse Actions" zip below it was silently dead code, since the
        # call site always passes plan=[]), so on a structurally similar later
        # command the model had nothing to ground itself in except recycled
        # prior narration, and would repeat/blend old phrasing instead of
        # describing what it actually just did this round.
        context = ("Recent Conversation History (for resolving references like "
                   "'it' / 'that file' / 'the second one' ONLY — never copy or "
                   "reuse phrasing from here; describe THIS round's real results):\n")
        for ex in self.history:
            context += f"User asked: {ex['user']}\n"
            if ex.get('results'):
                context += f"Actual outcomes: {ex['results']}\n"
            context += "\n"
        return context
