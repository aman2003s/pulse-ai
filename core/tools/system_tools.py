from typing import Dict, Any, ClassVar
from core.tools.registry import registry, Tool
from core.db import get_db

class ChangeSettingsTool(Tool):
    name: str = "change_settings"
    description: str = "Changes a system setting such as feedback_mode (Minimal, Standard, Guided)."
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "The setting key, e.g., 'feedback_mode'"},
            "value": {"type": "string", "description": "The new value"}
        },
        "required": ["key", "value"]
    }
    output_schema: Dict[str, Any] = {"type": "object"}
    permission_level: str = "safe"

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        key = params.get("key")
        value = params.get("value")

        conn = get_db()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, value)
            )
        return {"success": True, "key": key, "value": value}

class DescribeScreenTool(Tool):
    name: str = "describe_screen"
    description: str = "Describes what is on the user's screen right now: the focused window and other open windows. Use when the user asks what's on screen, what's open, or where they are."
    input_schema: Dict[str, Any] = {"type": "object", "properties": {}}
    output_schema: Dict[str, Any] = {"type": "object"}
    permission_level: str = "safe"

    IGNORE: Any = ("Program Manager", "Windows Input Experience", "Settings", "")

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        import win32gui
        focused = win32gui.GetWindowText(win32gui.GetForegroundWindow())
        titles = []
        def cb(h, _):
            if win32gui.IsWindowVisible(h):
                t = win32gui.GetWindowText(h)
                if t and t not in self.IGNORE and t != focused and t not in titles:
                    titles.append(t)
        win32gui.EnumWindows(cb, None)
        return {"success": True, "focused_window": focused or "nothing focused", "open_windows": titles[:10]}


# Module-level cache: index -> uiautomation.Control from the most recent read_screen,
# so a follow-up "click element 2" / "type into element 3" can target it. Valid for the
# current turn — a stale index (screen changed since) is reported as an error, not a crash.
_LAST_ELEMENTS: list = []

# Set once at startup (VoiceController.__init__) so LookAtScreenTool can build
# its own PlannerClient for vision calls — tools are constructed by the
# registry with no direct handle back to VoiceController's own planner
# instance, same reason _LAST_ELEMENTS is module-level state rather than
# threaded through ToolExecutor.
_PLANNER_PORT: int = 8081


def set_planner_port(port: int):
    global _PLANNER_PORT
    _PLANNER_PORT = port


def _control_view_children(c):
    # Researched (2026-07-28): the `uiautomation` package walks the RAW,
    # completely unfiltered UIA tree by default (its ViewWalker is hardcoded
    # to IUIAutomation.RawViewWalker — confirmed by reading the package
    # source; ControlViewWalker sits right there, just commented out). Real
    # screen readers (NVDA, JAWS, Narrator) never consume the raw tree — they
    # use the CONTROL VIEW, filtered by the standard IsControlElement/
    # IsContentElement properties that every properly-behaved app (including
    # Chromium/WebView2/Electron, which Word's trial dialog turned out to be)
    # already sets correctly so assistive tech skips its own decorative
    # chrome. Confirmed live: switching to ControlViewWalker for the exact
    # same noisy Word window cut ~230 raw items (cookie banners, zoom
    # controls, tab strips, save-card icons, duplicate caption buttons from
    # nested browser frames) down to ~140 genuinely meaningful ones, with the
    # dialog's real buttons surfacing right next to each other instead of
    # scattered among dozens of decoys. This is the OS-standard mechanism for
    # exactly this problem, not app-specific filtering — falls back to the
    # raw GetChildren() if anything about accessing the walker fails, so a
    # COM hiccup degrades to the old (noisier but working) behavior rather
    # than breaking the read entirely.
    try:
        from uiautomation import uiautomation as _auto_impl
        import uiautomation as _auto
        walker = _auto_impl._AutomationClient.instance().IUIAutomation.ControlViewWalker
        children = []
        elem = walker.GetFirstChildElement(c.Element)
        while elem:
            children.append(_auto.Control.CreateControlFromElement(elem))
            elem = walker.GetNextSiblingElement(elem)
        return children
    except Exception:
        return c.GetChildren()


def _walk_screen_tree(top):
    # Extracted so ReadScreenTool can re-run a full walk per retry attempt
    # (see the self-healing loop in ReadScreenTool.execute) rather than only
    # retrying the initial window probe.
    items, texts, elements = [], [], []
    try:
        win_rect = top.BoundingRectangle
    except Exception:
        win_rect = None

    def region_of(ch):
        # Researched (2026-07-27): a flat accessibility-tree text list has
        # no way to disambiguate multiple controls sharing the same label —
        # confirmed live, a Word dialog exposed TWO "Button: Close" entries
        # (the small dismiss-X on a promotional banner vs. the actual
        # titlebar close for the whole app), and picking the wrong one
        # risks quitting the app instead of dismissing a popup. Production
        # computer-use agents solve exactly this by grounding on-screen
        # POSITION alongside the accessibility label (Claude's own Computer
        # Use tool works via screen coordinates for this reason) — full
        # screenshots aren't needed for this, UIA already exposes each
        # control's on-screen rectangle for free; a coarse position tag is
        # enough to tell "top toolbar" from "corner of the whole window."
        #
        # width/height/xcenter/ycenter are METHODS on this uiautomation
        # version's Rect (confirmed via inspect.getsource — no @property),
        # not attributes. Root-caused live (2026-07-28): calling them bare
        # compared a bound method to an int and raised on the very first
        # line, before the try/except below — every single call silently
        # propagated up into the caller's except and discarded the ENTIRE
        # matched control, not just the position tag. That's why `controls`
        # kept coming back empty for any window with a real bounding
        # rectangle (i.e. almost always) ever since this function was added.
        try:
            if win_rect is None or win_rect.width() <= 0 or win_rect.height() <= 0:
                return ""
            r = ch.BoundingRectangle
            if r.width() <= 0 or r.height() <= 0:
                return ""
            rel_x = (r.xcenter() - win_rect.left) / win_rect.width()
            rel_y = (r.ycenter() - win_rect.top) / win_rect.height()
            col = "left" if rel_x < 0.33 else ("right" if rel_x > 0.67 else "center")
            row = "top" if rel_y < 0.33 else ("bottom" if rel_y > 0.67 else "middle")
            return f" @{row}-{col}"
        except Exception:
            return ""

    def walk(c, depth):
        # A generous runaway-safety valve, not a working constraint — sized
        # so it should never be the reason real content goes missing (a
        # WebView2-hosted dialog's actual buttons were found live at depth
        # 21; deep menus/trees in other apps can go further still). Only
        # exists to bound a genuinely pathological tree, same principle as
        # round budgets elsewhere: limit runaway repetition, not normal work.
        if depth > 40 or len(items) > 300:
            return
        for ch in _control_view_children(c):
            try:
                name = (ch.Name or "").strip()
                t = ch.ControlTypeName.replace("Control", "")
                # Edit/Document are always fill-targetable even with no name — an empty
                # text-entry area (e.g. Notepad's main content, which reports as
                # "Document" not "Edit") usually has no accessible label at all, but
                # it's exactly the thing a user wants to type into.
                if t in ("Button", "CheckBox", "RadioButton", "ComboBox", "MenuItem", "Hyperlink", "TabItem", "ListItem", "Edit", "Document", "TreeItem", "SplitButton"):
                    label = name if name else f"(unlabeled {t.lower()})"
                    elements.append(ch)
                    items.append(f"[{len(elements)}] {t}: {label[:80]}{region_of(ch)}")
                elif t == "Text" and name and len(name) > 1:
                    texts.append(name[:300])
                walk(ch, depth + 1)
            except Exception:
                continue

    walk(top, 0)
    return items, texts, elements


class ReadScreenTool(Tool):
    name: str = "read_screen"
    description: str = "Reads the CONTENTS of the focused window: its buttons, text fields, links, and visible text, each numbered. Use when the user asks to read the screen/page/window, what's available, or before clicking/filling something by number."
    input_schema: Dict[str, Any] = {"type": "object", "properties": {}}
    output_schema: Dict[str, Any] = {"type": "object"}
    permission_level: str = "safe"

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        # NOTE: focus-correction (ensure_target_focused) deliberately does NOT
        # live here — this tool is also called directly by the passive
        # background screen-context cache (_refresh_screen_cache), which runs
        # continuously and must never force any window to front. It's applied
        # instead at the one call site that's exclusively active AI task
        # execution (_execute_with_heartbeat in controller.py) — "on top only
        # when actually in use", not on every background poll.
        import uiautomation as auto
        import win32gui
        import time
        global _LAST_ELEMENTS
        # Self-healing retry for a confirmed transient state: right after
        # open_app, a heavy app (Word) can hold real OS-level foreground focus
        # (GetForegroundControl succeeds, not None — a genuinely different
        # case from the locked-screen check below) while its OWN UI is still
        # rendering, so the control tree walk finds a blank window name and
        # zero children. Live-confirmed via the raw log: read_screen returned
        # {"window": "", "controls": [], "visible_text": []} right after a
        # fresh open_app, and the model burned 3 rounds re-reading before the
        # loop-detector caught it — an avoidable delay, not a real obstacle.
        # Same pattern already used elsewhere (save_file's dialog-wait,
        # bring_app_to_front's window-wait): poll briefly for the transient
        # condition to resolve INSIDE the tool, rather than surfacing an
        # obviously-incomplete result and making the AI discover that by trial
        # and error. Generic to any slow-rendering app, not Word-specific.
        # A SEPARATE failure mode from the blank-window-name case above — the
        # window Name and status-bar text could already be populated (so the
        # probe below sees a "real" window and stops retrying) while the
        # interactive-controls walk still came back completely empty, every
        # single time, for a genuinely populated window. Chased through two
        # wrong hypotheses (a transient WebView2 IPC glitch, then a walk-depth
        # cutoff) before a direct UIA tree dump plus source inspection of the
        # `uiautomation` package found the real, simple bug: region_of() (in
        # _walk_screen_tree) called Rect.width/.height/.xcenter as bare
        # attributes, but this library version defines them as plain methods
        # — the comparison raised on every call, silently discarding the
        # ENTIRE matched control in the caller's except, not just the
        # position tag. Fixed there, not here; this retry loop is kept as a
        # legitimate backstop for genuine transient cases (it re-runs the
        # full walk, not just the window probe).
        win = None
        top = None
        items, texts, elements = [], [], []
        for attempt in range(4):
            win = auto.GetForegroundControl()
            if win is not None:
                top = win.GetTopLevelControl() or win
                try:
                    probed_children = len(top.GetChildren())
                except Exception:
                    probed_children = 0
                has_window = (top.Name or "").strip() or probed_children > 0
                if has_window:
                    items, texts, elements = _walk_screen_tree(top)
                    if items or texts:
                        break
            if attempt < 3:
                time.sleep(0.35)
        if win is None:
            # Distinguish a genuinely diagnosable state from a generic "try
            # again" — confirmed live: GetForegroundWindow() returning 0 means
            # there is NO foreground window in the entire session, which
            # happens when the workstation is locked (Windows deliberately
            # blocks foreground-window access during lock, for security) or
            # there's no active desktop session at all. Retrying read_screen
            # or open_app repeatedly can never fix this — it's not an app
            # being slow, it's the OS withholding window access entirely. A
            # specific message here lets this get reported plainly instead of
            # being treated like any other transient obstacle and retried
            # into the generic loop-detector.
            if win32gui.GetForegroundWindow() == 0:
                return {"error": "No window is focusable anywhere on this system right now — this usually means the screen is locked or there's no active desktop session. This can't be fixed by retrying; the user needs to unlock the screen."}
            return {"error": "Couldn't detect the focused window right now. Try again."}

        # _LAST_ELEMENTS is what fill_element/click_element index into by
        # number — it must only ever reflect the AI's OWN most recent
        # read_screen. Confirmed live: the passive background screen-context
        # cache (_refresh_screen_cache) also calls this tool, on its own
        # 0.5s-polling schedule, completely independent of any active task —
        # if it fires between the AI's read_screen and its next fill_element,
        # it silently overwrites this array out from under the AI, corrupting
        # the very indices it's about to use (seen live: fill_element on
        # index 1 hit a ButtonControl instead of the expected text editor).
        # The passive caller passes _update_index=False so it can still
        # describe the screen for context without touching this shared state.
        if params.get("_update_index", True):
            _LAST_ELEMENTS = elements
        # No separate [:50]/[:30] truncation here anymore — root-caused live
        # (2026-07-28) as a real, concrete failure, not a hypothetical one:
        # Word's own real editable document control landed at position
        # 124-125 out of 142 for a genuinely normal case (a rich ribbon UI
        # plus one promotional dialog), so the AI's OWN document was silently
        # cut from what it could see, and it fell back to guessing at the
        # first plausible "Document"-type match instead — which was the
        # dialog's own decorative content pane, not a text field at all.
        # `_walk_screen_tree`'s internal walk already has a genuine runaway
        # safety valve (300 items / depth 40); that's the only limit that
        # should exist here, sized for pathological trees, not normal
        # richly-populated windows. A 142-item result costs a few thousand
        # tokens against a 32K context budget — cheap next to silently
        # hiding the one control the task actually needed.
        return {"success": True, "window": top.Name,
                "controls": items, "visible_text": texts}


class LookAtScreenTool(Tool):
    name: str = "look_at_screen"
    description: str = (
        "Takes an ACTUAL screenshot of the focused window and visually analyzes it — use this when "
        "read_screen's list is ambiguous or you can't tell controls apart from their labels alone "
        "(e.g. two entries both called 'Close', or a plausible-sounding button that might be unrelated "
        "browser/app chrome rather than what you're actually trying to dismiss). Ask a specific question, "
        "e.g. 'which of these is the small X that closes just this popup, not the whole app, and where is "
        "it positioned?'. If the answer identifies a specific spot, click_at_position can click it directly "
        "even if it never appeared as a numbered element."
    )
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {"question": {"type": "string", "description": "What you want to know about what's visually on screen right now."}},
        "required": ["question"]
    }
    output_schema: Dict[str, Any] = {"type": "object"}
    permission_level: str = "safe"
    # Vision inference (screenshot capture + encode + analyze_image's own 60s
    # budget) is real extra work beyond the fast text-only tools — give this
    # more room than the 10s default so a genuine analysis isn't cut off
    # mid-flight by ToolExecutor's thread-join timeout.
    timeout_s: float = 70.0

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        import uiautomation as auto
        import base64
        import io
        from PIL import ImageGrab
        from core.planner.client import PlannerClient

        question = (params.get("question") or "").strip()
        if not question:
            return {"error": "No question given — say what you want to know about the screen."}

        win = auto.GetForegroundControl()
        if win is None:
            return {"error": "Couldn't detect the focused window right now."}
        top = win.GetTopLevelControl() or win
        try:
            r = top.BoundingRectangle
            bbox = (r.left, r.top, r.right, r.bottom)
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                raise ValueError("degenerate window rect")
        except Exception:
            bbox = None

        try:
            # Crop to just the focused window rather than the full screen —
            # keeps the image relevant to what the AI is actually working on
            # and smaller/faster to encode, same reasoning as why read_screen
            # only reads the focused window's own tree, not every window.
            img = ImageGrab.grab(bbox=bbox) if bbox else ImageGrab.grab()
        except Exception as e:
            return {"error": f"Couldn't capture a screenshot: {e}"}

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        image_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

        # Coordinates instructed as 0-1000 normalized, matching Gemma's
        # documented spatial-token convention (same scale used for Gemini's
        # object-detection/pointing output) — translated back to real screen
        # pixels here so click_at_position can use them directly without the
        # AI needing to do that math itself.
        full_question = (
            f"{question}\n\nIf your answer identifies a specific point to click, end your response with "
            f"a line in exactly this format: POINT: x,y — where x and y are 0-1000, representing the "
            f"position as a fraction of this image's width and height (0,0 is top-left, 1000,1000 is "
            f"bottom-right). Omit that line entirely if no specific point applies."
        )
        client = PlannerClient(port=_PLANNER_PORT)
        answer = client.analyze_image(image_b64, full_question)

        result = {"success": True, "answer": answer}
        if bbox:
            import re
            m = re.search(r"POINT:\s*(\d+)\s*,\s*(\d+)", answer)
            if m:
                nx, ny = int(m.group(1)), int(m.group(2))
                width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
                result["point_screen_position"] = {
                    "x": bbox[0] + round(width * nx / 1000),
                    "y": bbox[1] + round(height * ny / 1000)
                }
        return result


class ClickAtPositionTool(Tool):
    name: str = "click_at_position"
    description: str = (
        "Clicks at an exact screen pixel position. Use this ONLY for a position identified by "
        "look_at_screen (its point_screen_position) that has no corresponding numbered element from "
        "read_screen — e.g. an icon-only control the accessibility tree doesn't expose at all. Prefer "
        "click_element with a numbered index whenever one is available; this is the fallback for when "
        "nothing in that list matches what's actually visible on screen."
    )
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "Screen X pixel, e.g. from look_at_screen's point_screen_position"},
            "y": {"type": "integer", "description": "Screen Y pixel, e.g. from look_at_screen's point_screen_position"}
        },
        "required": ["x", "y"]
    }
    output_schema: Dict[str, Any] = {"type": "object"}
    permission_level: str = "safe"

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        import uiautomation as auto
        x, y = params.get("x"), params.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            return {"error": "x and y must be integers."}
        try:
            auto.Click(x, y)
            return {"success": True, "message": f"Clicked at ({x}, {y})"}
        except Exception as e:
            return {"error": str(e)}


class ClickElementTool(Tool):
    name: str = "click_element"
    description: str = "Clicks a numbered element from the most recent read_screen result (use the [N] number shown there — buttons, links, tabs, list items). Always call read_screen first if you don't already have current numbers."
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {"index": {"type": "integer", "description": "The [N] number from read_screen"}},
        "required": ["index"]
    }
    output_schema: Dict[str, Any] = {"type": "object"}
    permission_level: str = "safe"

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        idx = params.get("index", 0) - 1
        if idx < 0 or idx >= len(_LAST_ELEMENTS):
            return {"error": "That element number isn't available anymore. Read the screen again first."}
        el = _LAST_ELEMENTS[idx]
        try:
            name = el.Name
            el.SetFocus()
            # Prefer a REAL simulated mouse click over InvokePattern.Invoke() —
            # confirmed live: Invoke() can report success without the app
            # actually treating it as a genuine click (the save dialog's confirm
            # button returned {"success": true} but nothing was saved). A real
            # click at the control's clickable point is what every visible
            # control responds to, since that's indistinguishable from an actual
            # user click — this is also the standard accessibility-tooling
            # fallback (GetClickablePoint + simulated click) for exactly this
            # unreliability. Only fall back to Invoke if a real click isn't
            # possible (element has no valid bounding rectangle — off-screen,
            # zero-size, obscured).
            try:
                el.Click(simulateMove=False)
            except Exception:
                invoke = el.GetInvokePattern()
                if not invoke:
                    raise
                invoke.Invoke()
            return {"success": True, "message": f"Clicked {name}"}
        except Exception as e:
            return {"error": str(e)}


class FillElementTool(Tool):
    name: str = "fill_element"
    description: str = "Types text into a numbered field from the most recent read_screen result. Set submit=true to press Enter afterward (e.g. for a search box)."
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "index": {"type": "integer", "description": "The [N] number from read_screen"},
            "value": {"type": "string"},
            "submit": {"type": "boolean", "description": "Press Enter after typing (e.g. to submit a search)"}
        },
        "required": ["index", "value"]
    }
    output_schema: Dict[str, Any] = {"type": "object"}
    permission_level: str = "safe"

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        idx = params.get("index", 0) - 1
        value = params.get("value", "")
        if idx < 0 or idx >= len(_LAST_ELEMENTS):
            return {"error": "That element number isn't available anymore. Read the screen again first."}
        el = _LAST_ELEMENTS[idx]
        try:
            name = el.Name
            el.SetFocus()
            val_pattern = el.GetValuePattern()
            if val_pattern:
                val_pattern.SetValue(value)
            else:
                import keyboard as kb
                kb.write(value)
            if params.get("submit"):
                import keyboard as kb
                kb.send("enter")
            return {"success": True, "message": f"Typed into {name}"}
        except Exception as e:
            return {"error": str(e)}


class WebSearchTool(Tool):
    name: str = "web_search"
    description: str = "Searches the internet for current information (facts, news, definitions) you might not know offline. Only use when the question needs real-time or current info, not for opening apps/files."
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"]
    }
    output_schema: Dict[str, Any] = {"type": "object"}
    permission_level: str = "safe"

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        query = params.get("query", "")
        import socket
        try:
            socket.create_connection(("1.1.1.1", 53), timeout=2).close()
        except Exception:
            return {"online": False, "message": "No internet connection detected."}
        try:
            import httpx, re
            from urllib.parse import unquote, parse_qs, urlparse
            r = httpx.get("https://html.duckduckgo.com/html/", params={"q": query}, timeout=8,
                           headers={"User-Agent": "Mozilla/5.0"})
            snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', r.text, re.S)[:3]
            clean = [re.sub(r"<[^<]+?>", "", s).strip() for s in snippets]
            # Also return real result URLs so follow-up actions (downloading a file,
            # opening a page) can act on them instead of just hearing prose.
            links = []
            for m in re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S)[:5]:
                href, title = m
                # DDG wraps results in a redirect: //duckduckgo.com/l/?uddg=<real-url>&...
                q = parse_qs(urlparse(href).query).get("uddg", [None])[0]
                links.append({"title": re.sub(r"<[^<]+?>", "", title).strip()[:80],
                              "url": unquote(q) if q else href})
            if not clean and not links:
                return {"online": True, "message": "No search results found."}
            return {"success": True, "online": True, "results": clean, "links": links}
        except Exception as e:
            return {"online": True, "error": str(e)}


class SendKeysTool(Tool):
    name: str = "send_keys"
    description: str = "Sends a keyboard SHORTCUT to the focused app — a specific combo like 'ctrl+s' to save, 'ctrl+z' to undo, 'enter', 'escape', an arrow key. NEVER use this to type actual text content (a sentence, a filename, anything the user asked to write) — that ALWAYS goes through fill_element on a numbered element, never here."
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {"keys": {"type": "string", "description": "e.g. 'ctrl+s', 'enter', 'escape', 'ctrl+a'"}},
        "required": ["keys"]
    }
    output_schema: Dict[str, Any] = {"type": "object"}
    permission_level: str = "confirm"  # base default; needs_confirm() below narrows this per-key

    # Routine, expected keys — the whole POINT of this tool (its own description
    # says "most apps save with ctrl+s"). Confirming every single one of these was
    # a real, confirmed bug: it turned "save the file" (something the user just
    # explicitly asked for) into an extra "should I continue?" round every time,
    # which is exactly the friction Standard mode's DEFAULTABLE-vs-REQUIRED rule
    # is supposed to avoid. Confirmation stays for anything NOT in this list —
    # closing/quitting combos, select-all-then-implied-destructive sequences, etc.
    _SAFE_KEYS: ClassVar[set] = {
        "ctrl+s", "ctrl+z", "ctrl+y", "ctrl+shift+z", "enter", "escape", "esc",
        "ctrl+a", "ctrl+c", "ctrl+v", "ctrl+x", "tab", "ctrl+n", "ctrl+t",
        "ctrl+f", "ctrl+home", "ctrl+end", "home", "end",
        "up", "down", "left", "right", "page up", "page down", "pageup", "pagedown",
    }

    def needs_confirm(self, params: Dict[str, Any]) -> bool:
        keys = params.get("keys", "").strip().lower()
        return keys not in self._SAFE_KEYS

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        import keyboard as kb
        keys = params.get("keys", "")
        try:
            kb.send(keys)
            return {"success": True, "message": f"Sent {keys}"}
        except Exception as e:
            return {"error": str(e)}


class DownloadFileTool(Tool):
    name: str = "download_file"
    description: str = "Downloads a file from a URL into the user's Downloads folder (e.g. an installer found via web_search links). Returns the saved path so it can be opened/run next."
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Direct URL of the file to download"},
            "filename": {"type": "string", "description": "Optional filename to save as (inferred from URL if omitted)"}
        },
        "required": ["url"]
    }
    output_schema: Dict[str, Any] = {"type": "object"}
    permission_level: str = "confirm"  # fetching + saving an executable is worth a spoken confirm

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        import httpx, os, re
        from pathlib import Path
        url = params.get("url", "")
        name = params.get("filename") or os.path.basename(url.split("?")[0]) or "download.bin"
        name = re.sub(r'[<>:"/\\|?*]', "_", name)[:120]
        dest = Path.home() / "Downloads" / name
        try:
            with httpx.stream("GET", url, timeout=60, follow_redirects=True,
                               headers={"User-Agent": "Mozilla/5.0"}) as r:
                r.raise_for_status()
                total = 0
                with open(dest, "wb") as f:
                    for chunk in r.iter_bytes(65536):
                        f.write(chunk)
                        total += len(chunk)
            mb = round(total / 1_048_576, 1)
            return {"success": True, "path": str(dest), "message": f"Downloaded {name}, {mb} megabytes, saved to Downloads."}
        except Exception as e:
            if dest.exists():
                dest.unlink(missing_ok=True)
            return {"error": f"Download failed: {e}"}


class SaveFileTool(Tool):
    name: str = "save_file"
    description: str = "Saves the focused app's current document through the standard Windows Save dialog, start to finish: triggers ctrl+s, enters the full path, confirms, handles an overwrite prompt, and verifies the file really exists on disk. ALWAYS use this to save a document — never drive the save dialog manually with fill_element/click_element."
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "File name, e.g. notes.txt"},
            "folder": {"type": "string", "description": "Target folder: desktop, documents, downloads, pictures, or an absolute path. Defaults to desktop."}
        },
        "required": ["filename"]
    }
    output_schema: Dict[str, Any] = {"type": "object"}
    permission_level: str = "safe"
    # Worst case internally: 6s ctrl+s dialog wait + 6s ctrl+shift+s fallback
    # wait + ~1s typing/overwrite-prompt handling + 8s file-existence poll =
    # ~21s. The executor's default 10s timeout was confirmed live to fire
    # before this legitimately finishes, orphaning the (unkillable) thread and
    # costing a spurious error + wasted retry round even though the save
    # itself succeeded moments later. Margin above the ~21s theoretical max.
    timeout_s: float = 26.0

    # Deterministic save primitive. Research-confirmed design (2026-07-26):
    # (1) typing the FULL path into the dialog's filename field saves to exactly
    # that path regardless of the folder the dialog is showing — no sidebar
    # navigation needed; (2) UIA SetValue on that field is known-unreliable
    # (some apps read it via a different channel and keep the default name), so
    # the path is typed via real keystrokes into the focused field; (3) this is
    # how commercial automation tools handle save dialogs — one deterministic
    # action, not improvised per-step UI steering. The Windows common dialog is
    # shared by virtually every app, so this is generic, not Notepad-specific.
    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        import os, time
        from pathlib import Path
        import uiautomation as auto
        import keyboard as kb

        filename = (params.get("filename") or "").strip()
        if not filename:
            return {"error": "No filename given."}
        folder = (params.get("folder") or "desktop").strip()
        home = Path.home()
        known = {"desktop": home / "Desktop", "documents": home / "Documents",
                 "downloads": home / "Downloads", "pictures": home / "Pictures"}
        key = folder.lower().replace("my ", "").rstrip("\\/")
        base = known.get(key) or (Path(folder) if os.path.isabs(folder) else home / "Desktop")
        full_path = filename if os.path.isabs(filename) else str(base / filename)
        target = Path(full_path)
        existed_before = target.exists()

        def find_name_field():
            # SubName (substring), not Name (exact match) — confirmed live this
            # tool is NOT generic across apps otherwise: Notepad's common dialog
            # labels this field "File name:" (with colon), Word's own Save As
            # labels the identical control "File name" (no colon) — an exact
            # match silently found nothing in Word, misreporting "no dialog
            # appeared" when one genuinely had. A substring match is robust to
            # this kind of small labeling difference across apps generically,
            # rather than needing a special case enumerated per app.
            try:
                fg = auto.GetForegroundControl()
                top = fg.GetTopLevelControl() or fg
                edit = auto.EditControl(searchFromControl=top, SubName="File name", searchDepth=14)
                if edit.Exists(0, 0):
                    return edit
            except Exception:
                pass
            return None

        # Dialog may already be open (e.g. a prior ctrl+s); only trigger if not.
        field = find_name_field()
        if field is None:
            kb.send("ctrl+s")
            deadline = time.time() + 6
            while field is None and time.time() < deadline:
                time.sleep(0.25)
                field = find_name_field()
        if field is None:
            # No dialog after ctrl+s. If the target's already on disk, ctrl+s
            # just silently re-saved it under its own name — success, not an
            # error. Confirmed live: reporting an error here made the model
            # retry save_file in a loop on an already-saved file.
            if target.exists():
                return {"success": True, "path": full_path, "message": f"Saved to {full_path}"}
            # Otherwise the document already had a DIFFERENT name (confirmed
            # live: Windows 11 Notepad's own session-restore reopened an old
            # already-named tab even on a fresh launch) — plain ctrl+s silently
            # re-saves under THAT old name and never opens a dialog at all, no
            # matter how long we wait. Save As always forces the naming dialog
            # regardless of whether the document already has a name, so retry
            # with that before giving up.
            kb.send("ctrl+shift+s")
            deadline = time.time() + 6
            while field is None and time.time() < deadline:
                time.sleep(0.25)
                field = find_name_field()
            if field is None:
                return {"error": "No save dialog appeared after ctrl+s or ctrl+shift+s, and the file isn't on disk. This app may use a custom save flow — read_screen to check what actually happened."}

        try:
            field.SetFocus()
        except Exception:
            pass
        kb.send("ctrl+a")
        kb.write(full_path, delay=0.01)
        time.sleep(0.2)
        kb.send("enter")

        # Overwrite prompt only appears if the target already existed.
        if existed_before:
            time.sleep(0.7)
            try:
                fg = auto.GetForegroundControl()
                top = fg.GetTopLevelControl() or fg
                if "confirm" in (top.Name or "").lower() or find_name_field() is not None:
                    kb.send("alt+y")
            except Exception:
                pass

        deadline = time.time() + 8
        while time.time() < deadline:
            if target.exists():
                return {"success": True, "path": full_path, "message": f"Saved to {full_path}"}
            time.sleep(0.3)
        # Confirmed live (Word): some apps replace the standard Windows save
        # dialog with their OWN custom one. There the full-path trick fails
        # silently — the field treats the path as a literal name and saves to
        # whatever location IT has selected (e.g. OneDrive), often auto-
        # appending the extension too (name field shows ".docx" separately, so
        # typing "x.docx" yields "x.docx.docx"). No way to handle that
        # deterministically for every app's custom dialog — hand the model the
        # exact traps to avoid so it can drive the dialog it actually sees.
        return {"error": f"Entered and confirmed the save, but {full_path} never appeared on disk. This app likely uses its OWN custom save dialog where typed full paths don't work. Read the screen and use that dialog's actual controls: fill its name field with JUST the name — no extension if the dialog already displays one separately (else it doubles, e.g. x.docx.docx) — explicitly change its location to what the user asked for (custom dialogs often default elsewhere, e.g. OneDrive), then click its Save button and verify."}


registry.register(ChangeSettingsTool())
registry.register(DownloadFileTool())
registry.register(DescribeScreenTool())
registry.register(ReadScreenTool())
registry.register(LookAtScreenTool())
registry.register(ClickAtPositionTool())
registry.register(ClickElementTool())
registry.register(FillElementTool())
registry.register(WebSearchTool())
registry.register(SendKeysTool())
registry.register(SaveFileTool())
