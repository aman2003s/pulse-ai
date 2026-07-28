import httpx
import json
import logging

logger = logging.getLogger(__name__)

class PlannerClient:
    def __init__(self, port=8081, timeout=30.0):
        self.url = f"http://127.0.0.1:{port}/completion"
        # Separate endpoint for vision calls (see analyze_image) — llama-server's
        # raw /completion endpoint expects a hand-built Gemma chat-template
        # string (see prompt() below) with no standard way to embed an image.
        # Multimodal input is only supported through the OpenAI-compatible
        # /v1/chat/completions endpoint, which handles image token placement
        # and mmproj encoding internally — this is the documented, standard way
        # every OpenAI-compatible multimodal client (not just this one) sends
        # images to llama-server, not a Pulse-specific workaround.
        self.chat_url = f"http://127.0.0.1:{port}/v1/chat/completions"
        self.timeout = timeout

    def prompt(self, system_prompt: str, user_text: str, schema: dict) -> dict:
        prompt_str = f"<bos><start_of_turn>user\n{system_prompt}\n\nUSER INPUT: {user_text}<end_of_turn>\n<start_of_turn>model\n"
        logger.info(f"===PLANNER SEND=== USER INPUT: {user_text}")

        payload = {
            "prompt": prompt_str,
            "json_schema": schema,
            # 256 was already tight; adding task_step_done (a new required field)
            # increased the minimum output size, and a response that hits this cap
            # mid-JSON comes back truncated/invalid — confirmed live: "I'm having
            # trouble thinking right now" is PlannerClient's own JSONDecodeError
            # fallback, seen right after that schema change. Raised to 384, then to
            # 640 now that "reasoning" (also required, generated first) adds more
            # minimum output length on top of task_step_done — same truncation risk,
            # caught proactively this time instead of waiting to hit it live again.
            # Generation still stops as soon as the JSON is actually complete, so
            # this only gives headroom for the rare longer response, not added
            # latency on the typical short one. Raised again to 768: added two
            # more required fields (missing_info, missing_info_required). Raised
            # again to 896: added two more (expectation_met, expected_effect).
            "n_predict": 896,
            # Was 0.1. Confirmed via research: Gemma 4 has a documented low-
            # temperature repetition attractor (github.com/google-deepmind/gemma
            # issue #647) — LOWER temperature makes it MORE likely to loop/repeat,
            # the opposite of typical tuning intuition. 0.1 was likely a direct
            # contributor to the observed stuck-repeating-the-same-step behavior.
            # Grammar-constrained decoding (json_schema above) still guarantees
            # syntactically valid JSON regardless of temperature, so this doesn't
            # risk malformed output — it only affects which valid completion gets
            # picked.
            "temperature": 0.4,
            "cache_prompt": True
        }
        
        try:
            with httpx.Client() as client:
                resp = client.post(self.url, json=payload, timeout=self.timeout)
                resp.raise_for_status()
                data = resp.json()
                content = data.get("content", "")
                logger.info(f"===GEMMA RAW RESPONSE=== {content}")

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

    # Researched (2026-07-28): production computer-use agents (Claude's own
    # Computer Use tool, OpenAI's Operator, Gemini Computer Use) all ground
    # their actions in actual SCREENSHOTS, not just accessibility-tree text —
    # confirmed live this session that text-only grounding has a real,
    # unavoidable ceiling: a WebView-hosted dialog exposed a plausible-sounding
    # but WRONG decoy button ("Open in app", real Edge browser chrome) right
    # alongside the dialog's own real content, with no way to tell them apart
    # from label text alone. Gemma 4 E4B (already the model this app runs) is
    # explicitly multimodal with "screen and UI understanding" as a named
    # capability — this method is what lets Pulse actually use that, as a
    # fallback for exactly the cases text can't resolve, not a replacement for
    # the fast text-only path used on every round.
    def analyze_image(self, image_b64: str, question: str) -> str:
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                        {"type": "text", "text": question}
                    ]
                }
            ],
            # No JSON schema here deliberately — this is a focused visual Q&A
            # that feeds into the NEXT normal (schema-constrained) planning
            # round as a tool result, not a replacement for it. Keeping this
            # call free-form keeps a brand-new, less-tested code path simple.
            "temperature": 0.2,
            # Live-confirmed (2026-07-28): this model "thinks" on the chat-
            # completions endpoint by default — a separate reasoning_content
            # stream generated BEFORE the real content, unlike the fast
            # text-only prompt() path above (which sidesteps this entirely by
            # using the raw /completion endpoint's own prompt template). A
            # 200-token budget was consumed entirely by mid-thought reasoning
            # (finish_reason: "length", content: "") before any real answer
            # was ever produced. A one-sentence description used ~365 tokens
            # total once given room to actually finish — 1200 gives real
            # headroom for a harder disambiguation question, not just the
            # measured minimum.
            "max_tokens": 1200
        }
        try:
            # Image encoding through the vision projector is real extra work
            # on top of normal text decoding — longer budget than the fast
            # text-only prompt() calls above, which are tuned for a tight loop.
            with httpx.Client() as client:
                resp = client.post(self.chat_url, json=payload, timeout=60.0)
                resp.raise_for_status()
                data = resp.json()
                message = data["choices"][0]["message"]
                content = (message.get("content") or "").strip()
                if content:
                    return content
                # Safety net for the same truncation case, if max_tokens ever
                # gets hit again on a harder question: the reasoning trace at
                # least contains real visual analysis, better than nothing.
                reasoning = (message.get("reasoning_content") or "").strip()
                if reasoning:
                    logger.warning("analyze_image: content was empty, falling back to reasoning_content")
                    return reasoning
                return "(the model didn't produce a usable answer)"
        except httpx.TimeoutException:
            logger.error("PlannerClient.analyze_image timed out.")
            return "(couldn't analyze the screenshot in time)"
        except Exception as e:
            logger.error(f"PlannerClient.analyze_image error: {e}")
            return f"(couldn't analyze the screenshot: {e})"
