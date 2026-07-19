import sys
import os
import subprocess
import time
from typing import Dict, Any

# Add the project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.tools.registry import registry, Tool
from core.planner.client import PlannerClient
from core.planner.prompts import get_system_prompt
from core.executor.executor import ToolExecutor

class DummyWeatherTool(Tool):
    name: str = "get_weather"
    description: str = "Gets the current weather for a specified city."
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "The name of the city"}
        },
        "required": ["city"]
    }
    output_schema: Dict[str, Any] = {"type": "object"}
    permission_level: str = "safe"

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        city = params.get("city", "Unknown")
        return {"weather": "Sunny", "temperature": 75, "city": city}

# Register the dummy tool
registry.register(DummyWeatherTool())

from core.tools.win_tools import *

def main():
    MODELS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'models'))
    MODEL_PATH = os.path.join(MODELS_DIR, 'gemma-4-E4B-it-Q4_K_M.gguf')
    SERVER_EXE = os.path.join(MODELS_DIR, 'llama-server.exe')

    if not os.path.exists(SERVER_EXE):
        print("llama-server not found. Run scripts/fetch_models.py first.")
        sys.exit(1)

    print("Starting llama-server...")
    process = subprocess.Popen([
        SERVER_EXE,
        "-m", MODEL_PATH,
        "--port", "8081",
        "-c", "2048",
    ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='ignore')
    
    ready = False
    while True:
        line = process.stdout.readline()
        if not line:
            break
        if "listening on http" in line or "HTTP server listening" in line or "llama server listening at" in line:
            ready = True
            break
        if process.poll() is not None:
            break

    if not ready:
        print("llama-server failed to start.")
        sys.exit(1)

    try:
        planner = PlannerClient(port=8081)
        executor = ToolExecutor()
        
        system_prompt = get_system_prompt(feedback_mode="Standard")
        schema = registry.get_planner_schema()
        
        test_queries = [
            "Can you search for any files named 'resume'?",
            "Open the second one you found."
        ]
        
        from core.conversation import ConversationManager
        conversation = ConversationManager()
        
        for q in test_queries:
            print(f"\nUser: {q}")
            print("Planner thinking...")
            
            system_prompt = get_system_prompt(feedback_mode="Standard")
            
            context_str = conversation.get_context_string()
            if context_str:
                system_prompt += f"\n\n{context_str}\n\nIMPORTANT: Use the history above to resolve ambiguous references (like 'the second one', 'that file', 'it')."
            
            plan = planner.prompt(
                user_text=q,
                system_prompt=system_prompt,
                schema=schema
            )
            print(f"Planner raw response: {plan}")
            
            if not plan:
                print("Planner failed.")
                continue
                
            speak_text = plan.get("speak", "")
            if speak_text:
                print(f"Pulse says: {speak_text}")
                
            steps = plan.get("plan", [])
            step_results = []
            for step in steps:
                tool_name = step.get("tool")
                params = step.get("params", {})
                print(f"Pulse wants to use tool: {tool_name} with params {params}")
                
                # Check for confirmation requirement
                needs_confirmation = step.get("needs_confirmation", False)
                if needs_confirmation:
                    print("Pulse is waiting for confirmation...")
                    user_confirmed = True
                else:
                    user_confirmed = True
                    
                result, status = executor.execute(tool_name, params, user_confirmed)
                print(f"Tool execution status: {status}")
                print(f"Tool execution result: {result}")
                step_results.append(result)
            else:
                if not steps:
                    print("No tool called.")
                    
            conversation.add_exchange(q, plan, step_results)

    finally:
        print("\nKilling llama-server...")
        process.kill()

if __name__ == "__main__":
    main()
