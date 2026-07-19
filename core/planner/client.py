import httpx
import json
import logging

logger = logging.getLogger(__name__)

class PlannerClient:
    def __init__(self, port=8081, timeout=30.0):
        self.url = f"http://127.0.0.1:{port}/completion"
        self.timeout = timeout

    def prompt(self, system_prompt: str, user_text: str, schema: dict) -> dict:
        prompt_str = f"<bos><start_of_turn>user\n{system_prompt}\n\nUSER INPUT: {user_text}<end_of_turn>\n<start_of_turn>model\n"
        
        payload = {
            "prompt": prompt_str,
            "json_schema": schema,
            "n_predict": 256,
            "temperature": 0.1,
            "cache_prompt": True
        }
        
        try:
            with httpx.Client() as client:
                resp = client.post(self.url, json=payload, timeout=self.timeout)
                resp.raise_for_status()
                data = resp.json()
                content = data.get("content", "")
                
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    logger.error(f"Failed to parse JSON response: {content}")
                    return {"speak": "I'm having trouble thinking right now. Please try again.", "plan": [], "needs_confirmation": False}
                    
        except httpx.TimeoutException:
            logger.error("PlannerClient timed out.")
            return {"speak": "I'm sorry, that took too long for me to process.", "plan": [], "needs_confirmation": False}
        except httpx.RequestError as e:
            logger.error(f"PlannerClient request error: {e}")
            return {"speak": "I'm having trouble connecting to my brain.", "plan": [], "needs_confirmation": False}
        except Exception as e:
            logger.error(f"PlannerClient error: {e}")
            return {"speak": "An unexpected error occurred.", "plan": [], "needs_confirmation": False}
