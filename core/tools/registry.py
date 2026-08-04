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
    # Per-tool override for ToolExecutor's hard timeout. Most tools finish in
    # well under a second, but one flat number doesn't fit everything — a tool
    # that legitimately polls for a UI change (e.g. waiting for a save dialog,
    # then waiting for the file to land on disk) needs real wall-clock budget
    # for that, or the executor's thread.join() gives up and reports "timed
    # out" while the (unkillable, in Python) thread keeps running in the
    # background — confirmed live: save_file's own thread completed the real
    # save well after the executor had already told the model it failed,
    # costing a spurious error and a wasted retry round.
    timeout_s: float = 10.0
    
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
                # Bounded reasoning space, generated FIRST (property order = generation
                # order for llama.cpp's schema-to-grammar conversion). This is what lets
                # Gemma actually use its reasoning ability without disabling grammar
                # enforcement — llama.cpp's own dedicated "thinking" mode does that
                # (confirmed: enabling it turns grammar constraints off entirely, which
                # would reintroduce the invalid-JSON risk already fixed). A plain schema
                # field sidesteps that entirely: still 100% grammar-constrained, still
                # guaranteed valid JSON, but the model gets real space to notice things
                # like "I already tried this twice" before committing to plan/speak.
                "reasoning": {
                    "type": "string",
                    # A maxLength of 2400 was tried here (2026-08-04) and broke EVERY
                    # planner call with HTTP 400 "Failed to initialize samplers: failed
                    # to parse grammar" -- root-caused, not guessed: this is a known,
                    # open llama.cpp bug (github.com/ggml-org/llama.cpp issues #25746 /
                    # #25923) -- json-schema-to-grammar's repetition cap for a string
                    # maxLength is only applied at the top level; any maxLength >= 2000
                    # (GRAMMAR_MAX_REPETITION_THRESHOLD, exactly) produces invalid GBNF
                    # once combined with a schema this complex, and because every tool
                    # call compiles into ONE combined grammar, that one bad field broke
                    # every single request, not just long-reasoning ones. No merged fix
                    # exists upstream as of this build (10068/571d0d540). Confirmed the
                    # exact boundary empirically against this real schema on a
                    # standalone llama-server: maxLength=1999 -> 200 OK, 2000 -> 400.
                    # 1900 stays safely under that with margin, and is still far more
                    # headroom than a normal round's reasoning ever needs -- a pure
                    # backstop against genuinely unbounded runaway generation, not the
                    # primary fix (that's n_predict headroom, see client.py).
                    "maxLength": 1900,
                    "description": "Internal reasoning before deciding — what you observe from the latest results, whether you're repeating yourself, and why this next step is the right one. Not shown to the user. Use the space you genuinely need, but don't spiral into repeating the same uncertainty over and over — once you've reasoned through it, commit to a decision."
                },
                # Forces an explicit action-effect check EVERY round, not just
                # after code notices a pattern (loop detection only catches
                # exact repeats after the fact). Confirmed live: clicking a
                # descriptive list item instead of the real action button left
                # a dialog completely unchanged, and the model re-read the
                # screen three times without ever explicitly registering "my
                # last click did nothing" — it only got flagged because the
                # repeated read_screen calls happened to be identical. Naming
                # what you expected, then being required to check it against
                # what actually happened, catches this the round it happens
                # instead of several rounds of confusion later.
                "expectation_met": {
                    "type": "string",
                    "description": "Compare what you expected to happen (stated as 'expected_effect' last round) against what the tool results actually show now. 'yes' if it matches. Otherwise say plainly what actually happened instead (e.g. 'no - the screen looks identical to before, that click did nothing'). Use 'n/a' only on the very first round of a task, before any action of yours has run yet."
                },
                # Explicit, code-checked gap-tracking — replaces silently deciding
                # to proceed on an assumption inside free-text reasoning, which is
                # what let a save happen with a made-up filename/location without
                # ever asking, even though a rule said to ask once first. The
                # model still uses its own judgment for WHAT counts as missing;
                # code enforces that a real, once-only question actually happens
                # for it before any assumption is acted on.
                "missing_info": {
                    "type": "string",
                    "description": "The ONE specific piece of information genuinely missing from the user's request that this step needs (e.g. 'file name and save location', 'message recipient'). Empty string if nothing is missing."
                },
                "missing_info_required": {
                    "type": "boolean",
                    "description": "Only meaningful when missing_info is non-empty. true = no safe default exists, the task cannot proceed without an answer (e.g. a recipient). false = a safe default exists (e.g. Desktop + a sensible filename) — you may proceed on it, but only after asking once."
                },
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
                # Paired with expectation_met above — states what should be
                # observably different NEXT round if THIS round's plan
                # actually succeeds, so there's something concrete to check
                # against instead of just hoping the action worked.
                "expected_effect": {
                    "type": "string",
                    "description": "What should be different/visible next round if this round's plan succeeds — e.g. 'the trial dialog should be gone, replaced by a blank document'. Empty string only when plan is empty (nothing to expect an effect from)."
                },
                "needs_confirmation": {"type": "boolean"},
                # Explicit, mandatory completion signal — replaces inferring "done"
                # from an empty plan, which is what let a task declare itself
                # finished right after typing text, without ever saving. true only
                # when a tool result has actually CONFIRMED the current step/goal is
                # complete; false whenever more work remains, including while
                # waiting on a clarifying question's answer.
                "task_step_done": {"type": "boolean"}
            },
            "required": ["reasoning", "expectation_met", "missing_info", "missing_info_required", "speak", "plan", "expected_effect", "needs_confirmation", "task_step_done"]
        }

    def get_qa_schema(self) -> dict:
        """Deliberately minimal schema for the Q&A fast path (5.1) — reasoning
        + speak + an optional single tool call, none of the multi-step-
        task bookkeeping fields (task_list/expected_effect/missing_info/
        task_step_done/etc.) a direct question never needs. Fewer required
        fields means less minimum generation length before the JSON
        completes — this session's own timing instrumentation confirmed
        generation length (not prompt processing, already cache-warm) is
        what actually dominates round time, so this is a direct lever on
        that, not just a smaller prompt. Tool choices (5.3): web_search for
        real-world facts, read_screen/look_at_screen for "summarize/describe
        what's on screen" style requests, search_file/read_pdf for "summarize/
        what does this PDF say" requests (search_file first if only given a
        name, not a path) — same lean lane, no task_list machinery needed for
        any of them."""
        return {
            "type": "object",
            "properties": {
                "reasoning": {
                    "type": "string",
                    "description": "Brief: do you already know this confidently, or does it need a live web_search / a look at the screen? Not shown to the user."
                },
                "speak": {
                    "type": "string",
                    "description": "The natural-language answer (1-3 sentences), or what you're about to check if calling a tool this round."
                },
                "plan": {
                    "type": "array",
                    "maxItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {"type": "string", "enum": ["web_search", "read_screen", "look_at_screen", "search_file", "read_pdf"]},
                            "params": {"type": "object"}
                        },
                        "required": ["tool", "params"]
                    }
                }
            },
            "required": ["reasoning", "speak", "plan"]
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
