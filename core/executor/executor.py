import logging
import threading
from typing import Dict, Any, Tuple
from core.tools.registry import registry

logger = logging.getLogger(__name__)

class ToolExecutor:
    def __init__(self):
        pass
        
    def execute(self, tool_name: str, params: Dict[str, Any], user_confirmed: bool = False) -> Tuple[Dict[str, Any], str]:
        """
        Executes a tool and returns (result_dict, status_string).
        status_string can be "success", "error", "needs_confirmation", "denied"
        """
        tool = registry.get_tool(tool_name)
        if not tool:
            return {"error": f"Tool '{tool_name}' not found."}, "error"
            
        if tool.permission_level == "dangerous":
            return {"error": "This action is considered dangerous and is refused in the current MVP."}, "denied"
            
        if tool.needs_confirm(params) and not user_confirmed:
            return {"message": f"Confirmation required to run {tool_name}."}, "needs_confirmation"
            
        # Optional: validate params against tool.input_schema using jsonschema here
        
        result = {}
        error = None
        
        # Run tool with a per-tool timeout using a thread (see Tool.timeout_s).
        def target():
            nonlocal result, error
            try:
                result = tool.execute(params)
            except Exception as e:
                logger.error(f"Error executing {tool_name}: {e}")
                error = str(e)

        thread = threading.Thread(target=target)
        thread.start()
        thread.join(timeout=tool.timeout_s)

        if thread.is_alive():
            # Timeout
            return {"error": f"Tool {tool_name} timed out after {tool.timeout_s:.0f} seconds."}, "error"
            
        if error:
            return {"error": error}, "error"
            
        # M2.5 Observer verification
        try:
            verified = tool.verify(params, result)
            result["verified"] = verified
        except Exception as e:
            logger.error(f"Error verifying {tool_name}: {e}")
            result["verified"] = False
            
        return result, "success"
