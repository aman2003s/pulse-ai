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


class ReadScreenTool(Tool):
    name: str = "read_screen"
    description: str = "Reads the CONTENTS of the focused window: its buttons, text fields, links, and visible text, each numbered. Use when the user asks to read the screen/page/window, what's available, or before clicking/filling something by number."
    input_schema: Dict[str, Any] = {"type": "object", "properties": {}}
    output_schema: Dict[str, Any] = {"type": "object"}
    permission_level: str = "safe"

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        import uiautomation as auto
        global _LAST_ELEMENTS
        win = auto.GetForegroundControl()
        if win is None:
            return {"error": "Couldn't detect the focused window right now. Try again."}
        top = win.GetTopLevelControl() or win
        items, texts, elements = [], [], []

        def walk(c, depth):
            if depth > 6 or len(items) > 40:
                return
            for ch in c.GetChildren():
                try:
                    name = (ch.Name or "").strip()
                    t = ch.ControlTypeName.replace("Control", "")
                    # Edit/Document are always fill-targetable even with no name — an empty
                    # text-entry area (e.g. Notepad's main content, which reports as
                    # "Document" not "Edit") usually has no accessible label at all, but
                    # it's exactly the thing a user wants to type into.
                    if t in ("Button", "CheckBox", "RadioButton", "ComboBox", "MenuItem", "Hyperlink", "TabItem", "ListItem", "Edit", "Document"):
                        label = name if name else f"(unlabeled {t.lower()})"
                        elements.append(ch)
                        items.append(f"[{len(elements)}] {t}: {label[:60]}")
                    elif t == "Text" and name and len(name) > 1:
                        texts.append(name[:200])
                    walk(ch, depth + 1)
                except Exception:
                    continue

        walk(top, 0)
        _LAST_ELEMENTS = elements
        return {"success": True, "window": top.Name,
                "controls": items[:25], "visible_text": texts[:15]}


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
            invoke = el.GetInvokePattern()
            if invoke:
                invoke.Invoke()
            else:
                el.Click(simulateMove=False)
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
    description: str = "Sends a keyboard shortcut to the focused app, e.g. 'ctrl+s' to save, 'ctrl+z' to undo, 'enter', 'escape'. Use for actions with no clickable button visible (most apps save with ctrl+s)."
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {"keys": {"type": "string", "description": "e.g. 'ctrl+s', 'enter', 'escape', 'ctrl+a'"}},
        "required": ["keys"]
    }
    output_schema: Dict[str, Any] = {"type": "object"}
    permission_level: str = "confirm"  # can trigger dialogs/overwrite files — worth a spoken confirm

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


registry.register(ChangeSettingsTool())
registry.register(DownloadFileTool())
registry.register(DescribeScreenTool())
registry.register(ReadScreenTool())
registry.register(ClickElementTool())
registry.register(FillElementTool())
registry.register(WebSearchTool())
registry.register(SendKeysTool())
