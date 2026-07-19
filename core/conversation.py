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
            
        context = "Recent Conversation History:\n"
        for ex in self.history:
            context += f"User: {ex['user']}\n"
            if ex['assistant'].get('speak'):
                context += f"Pulse (spoken): {ex['assistant']['speak']}\n"
            plan = ex['assistant'].get('plan', [])
            if plan:
                context += "Pulse Actions:\n"
                for step, res in zip(plan, ex['results']):
                    context += f"- {step['tool']}({step['params']}) -> {res}\n"
            context += "\n"
        return context
