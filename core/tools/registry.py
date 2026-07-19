from pydantic import BaseModel, Field
from typing import Dict, Any, Type, Optional, Callable
import json

class Tool(BaseModel):
    name: str
    description: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    permission_level: str = "safe" # "safe", "confirm", "dangerous"
    platforms: list[str] = ["win32"]
    
    # In a real implementation, we could just attach a callable or subclass
    # Here we define an execute method for subclasses to override
    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError()

    def needs_confirm(self, params: Dict[str, Any]) -> bool:
        """Param-aware confirmation: lets a tool waive the spoken confirm for
        provably-safe cases (e.g. closing an Explorer window is non-destructive,
        force-killing an app process is not)."""
        return self.permission_level == "confirm"
        
    def verify(self, params: Dict[str, Any], result: Dict[str, Any]) -> bool:
        """Override to implement M2.5 Observer verification (e.g. process exists, file opened)"""
        return True

class ToolRegistry:
    def __init__(self):
        self.tools: Dict[str, Tool] = {}
        
    def register(self, tool: Tool):
        self.tools[tool.name] = tool
        
    def get_tool(self, name: str) -> Optional[Tool]:
        return self.tools.get(name)
        
    def get_all_tools(self) -> list[Tool]:
        return list(self.tools.values())
        
    def get_planner_schema(self) -> dict:
        """Generates the JSON schema for the planner to use to output tool calls."""
        tool_names = list(self.tools.keys())
        
        return {
            "type": "object",
            "properties": {
                "speak": {"type": "string"},
                # For complex multi-part goals: a spoken-language breakdown of the whole
                # job into sequential steps. The executor persists it, narrates progress
                # ("Step 2 of 5..."), and runs each step through its own observe/re-plan
                # loop — the plan-and-execute agent pattern.
                "task_list": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "plan": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {
                                "type": "string",
                                "enum": tool_names
                            },
                            "params": {"type": "object"}
                        },
                        "required": ["tool", "params"]
                    }
                },
                "needs_confirmation": {"type": "boolean"}
            },
            "required": ["speak", "plan", "needs_confirmation"]
        }
        
    def get_system_prompt_tools_text(self) -> str:
        if not self.tools:
            return "No tools available."
            
        text = "Available Tools:\n"
        for name, tool in self.tools.items():
            text += f"- {name}: {tool.description}\n"
            text += f"  Params schema: {json.dumps(tool.input_schema)}\n"
        return text

# Global registry
registry = ToolRegistry()
