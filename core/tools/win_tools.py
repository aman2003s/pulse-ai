import os
import time
import threading
import psutil
from rapidfuzz import process, fuzz
from typing import Dict, Any, ClassVar
import json
from pathlib import Path
from core.db import get_db
from core.tools.registry import registry, Tool
from core.adapters.win.focus import bring_app_to_front, bring_explorer_folder_to_front, find_existing_window, _force_foreground, remember_target

class OpenAppTool(Tool):
    name: str = "open_app"
    description: str = "Opens a Windows application (like Chrome, Notepad, Word)."
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The name of the app to open"}
        },
        "required": ["name"]
    }
    output_schema: Dict[str, Any] = {"type": "object"}
    permission_level: str = "safe"

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        app_name = params.get("name", "").lower()
        
        conn = get_db()
        apps = conn.execute("SELECT * FROM app_index").fetchall()
        
        names_to_paths = {}
        for row in apps:
            names_to_paths[row["name"]] = row["path"]
            aliases = json.loads(row["aliases"])
            for alias in aliases:
                names_to_paths[alias] = row["path"]
                
        if not names_to_paths:
            return {"error": "App index is empty. It needs to be built first."}
            
        choices = list(names_to_paths.keys())
        match = process.extractOne(app_name, choices, scorer=fuzz.QRatio)
        
        if not match or match[1] < 60:
            # Fallback: try raw name directly (e.g. "notepad.exe" or "calc")
            name_hint = app_name.split()[0]
            # Reuse-if-already-open, checked BEFORE ever launching — confirmed
            # real bug otherwise: every "open X" kept spawning a new window/tab
            # even when X was already open. The AI gets told this explicitly
            # (already_running) so it can decide whether the existing window is
            # safe to type into or a new tab is warranted, rather than us
            # silently guessing either way.
            existing = find_existing_window(name_hint)
            if existing:
                _force_foreground(existing)
                remember_target(existing)
                return {"success": True, "app": app_name, "path": "System PATH", "already_running": True}
            try:
                launched_at = time.time()
                os.startfile(app_name)
                # Block until actually foregrounded (was fire-and-forget on a
                # background thread) — confirmed live via raw prompt/response trace:
                # a read_screen in the SAME round right after open_app was racing
                # ahead of this and reading whichever window had focus BEFORE the
                # launch, twice in a row, before the real window ever appeared.
                # open_app's success result is supposed to mean "ready to interact
                # with" — returning before that's true was the actual bug.
                bring_app_to_front(name_hint, launched_at)
                remember_target(find_existing_window(name_hint))
                return {"success": True, "app": app_name, "path": "System PATH", "already_running": False}
            except Exception:
                return {"error": f"Could not find an app matching '{app_name}'."}

        best_name = match[0]
        app_path = names_to_paths[best_name]
        name_hint = best_name.split()[0]

        existing = find_existing_window(name_hint)
        if existing:
            _force_foreground(existing)
            remember_target(existing)
            return {"success": True, "app": best_name, "path": app_path, "already_running": True}

        try:
            launched_at = time.time()
            os.startfile(app_path)
            # See note above — block until foregrounded instead of racing read_screen.
            bring_app_to_front(name_hint, launched_at)
            remember_target(find_existing_window(name_hint))
            return {"success": True, "app": best_name, "path": app_path, "already_running": False}
        except Exception as e:
            return {"error": str(e)}

class CloseAppTool(Tool):
    name: str = "close_app"
    description: str = "Closes a running Windows application."
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The name of the app to close (e.g. notepad.exe)"}
        },
        "required": ["name"]
    }
    output_schema: Dict[str, Any] = {"type": "object"}
    permission_level: str = "confirm" # Important: close app needs confirmation

    def needs_confirm(self, params: Dict[str, Any]) -> bool:
        # Closing Explorer WINDOWS is non-destructive (WM_CLOSE, no data at risk) —
        # no spoken confirmation needed. Force-killing an app process still confirms.
        name = params.get("name", "").lower().replace(".exe", "").strip()
        return name not in ("explorer", "file explorer", "windows explorer")

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        app_name = params.get("name", "").lower().replace(".exe", "").strip()

        # File Explorer windows live inside the Windows shell process (explorer.exe) —
        # terminating that kills the taskbar and desktop. Close the folder WINDOWS
        # (CabinetWClass) gracefully instead. Found via real testing: "close explorer"
        # failed/was dangerous under the old kill-by-process-name approach.
        if app_name in ("explorer", "file explorer", "windows explorer"):
            import win32gui, win32con
            hwnds = []
            win32gui.EnumWindows(lambda h, _: hwnds.append(h) if win32gui.GetClassName(h) == "CabinetWClass" else None, None)
            for h in hwnds:
                win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
            if hwnds:
                return {"success": True, "closed_windows": len(hwnds), "message": f"Closed {len(hwnds)} File Explorer window(s)."}
            return {"error": "No File Explorer windows are open."}

        exe = app_name + ".exe"
        running = {}  # lowercased real process name -> list of psutil.Process
        for proc in psutil.process_iter(['name']):
            n = proc.info['name']
            if n:
                running.setdefault(n.lower(), []).append(proc)

        # Confirmed live (2026-07-31, "close calculator" failing) and independently
        # documented (Microsoft's own PowerToys has an open issue about exactly
        # this): modern UWP apps' real process name routinely doesn't match the
        # friendly name + ".exe" guess — Calculator runs as CalculatorApp.exe, not
        # calculator.exe, and which UWP apps get their own named process vs. run
        # hosted under ApplicationFrameHost.exe varies app to app and Windows
        # version to version, so no fixed alias table stays correct. Same
        # rapidfuzz.process.extractOne pattern OpenAppTool already uses for the
        # equivalent friendly-name-to-real-target problem, applied here against
        # the REAL running process list instead of the app index (closing only
        # cares about what's actually running right now). Exact match tried
        # first — cheap, and guarantees this never behaves differently for a
        # name that already matches exactly.
        target_procs = running.get(exe)
        if not target_procs:
            match = process.extractOne(app_name, list(running.keys()), scorer=fuzz.QRatio)
            if match and match[1] >= 60:
                target_procs = running[match[0]]

        # Real gap found in review (2026-08-01): this used to force-kill every
        # non-Explorer app unconditionally (proc.terminate() == TerminateProcess,
        # a hard kill) while Explorer's own windows get a graceful WM_CLOSE just
        # above — meaning an app with unsaved work never got the chance to show
        # its own "save changes?" prompt the way clicking its window's X would.
        # Fixed the same way: WM_CLOSE every top-level window the process owns
        # first, and give it a moment to exit on its own. If a save-prompt (or
        # anything else) pops up, that's a genuinely new window — the EXISTING
        # generic mid-task dialog detection (_execute_with_heartbeat's
        # _new_window_appeared, prompt rule 18) already catches exactly this on
        # the next round and hands it to the planner's own judgment (read it,
        # narrate it, ask the user) rather than this tool guessing what to do
        # with a hardcoded timeout-then-kill. Force-kill is now the fallback
        # only for processes WM_CLOSE can't reach at all (no top-level window —
        # a headless/background process), not the default for every app.
        import win32gui, win32con, win32process

        def _close_windows_for_pid(pid):
            hwnds = []
            def _enum(h, _):
                if win32gui.IsWindowVisible(h):
                    _, found_pid = win32process.GetWindowThreadProcessId(h)
                    if found_pid == pid:
                        hwnds.append(h)
            win32gui.EnumWindows(_enum, None)
            for h in hwnds:
                win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
            return hwnds

        closed = 0
        still_running = []
        if target_procs:
            for proc in target_procs:
                try:
                    hwnds = _close_windows_for_pid(proc.pid)
                    if hwnds:
                        try:
                            proc.wait(timeout=1.5)
                            closed += 1
                            continue
                        except psutil.TimeoutExpired:
                            # Didn't exit after a graceful request — likely showing
                            # its own dialog (unsaved changes, etc.) and waiting on
                            # the user. Leave it running rather than killing it out
                            # from under that dialog; the new-window detection above
                            # surfaces it for the planner to actually look at.
                            still_running.append(proc.pid)
                            continue
                    # No visible top-level window to close gracefully — a
                    # background/headless process WM_CLOSE can't reach.
                    proc.terminate()
                    closed += 1
                except Exception:
                    pass

        if still_running:
            return {"success": True, "closed_processes": closed,
                     "note": "Close requested — one or more windows are still open, possibly waiting on an unsaved-changes prompt. Check the screen before assuming it's done."}
        if closed > 0:
            return {"success": True, "closed_processes": closed}
        return {"error": f"No running process found matching '{exe}'."}

class SearchFileTool(Tool):
    name: str = "search_file"
    description: str = "Searches for a file by name. Fast index of common folders first (Desktop, Documents, Downloads, Pictures), then a live filesystem search as fallback. Pass 'location' to search a specific folder (e.g. 'downloads', 'C:/Projects')."
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The file name to search for"},
            "location": {"type": "string", "description": "Optional folder to search in: a known name (desktop/documents/downloads/pictures) or an absolute path"}
        },
        "required": ["query"]
    }
    output_schema: Dict[str, Any] = {"type": "object"}
    permission_level: str = "safe"

    SKIP_DIRS: ClassVar[set] = {"node_modules", "appdata", "__pycache__", ".git", "venv", "$recycle.bin", "windows", "program files", "program files (x86)"}

    def _resolve_location(self, loc: str) -> str:
        home = Path.home()
        key = loc.strip().lower().replace("my ", "").rstrip("\\/")
        known = {"desktop", "documents", "downloads", "pictures", "music", "videos"}
        if key in known:
            return str(home / key.capitalize())
        p = os.path.expandvars(os.path.expanduser(loc))
        return p if os.path.isdir(p) else str(home)

    def _live_walk(self, query: str, root: str, budget_s: float = 6.0, max_hits: int = 8):
        """Bounded live search for anything the index doesn't cover — depth- and
        time-capped so a huge tree can't hang the assistant."""
        hits = []
        t0 = time.time()
        root_depth = root.rstrip("\\/").count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            if time.time() - t0 > budget_s or len(hits) >= max_hits:
                break
            if dirpath.rstrip("\\/").count(os.sep) - root_depth >= 5:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d.lower() not in self.SKIP_DIRS and not d.startswith(".")]
            # Folders are search hits too ("open my email folder") — the old version
            # only matched files, so a folder by name was literally unfindable.
            for dn in dirnames:
                score = fuzz.partial_ratio(query, dn.lower())
                if score >= 70:
                    hits.append({"name": dn, "path": os.path.join(dirpath, dn), "score": score, "type": "folder"})
            for fn in filenames:
                score = fuzz.partial_ratio(query, fn.lower())
                if score >= 70:
                    hits.append({"name": fn, "path": os.path.join(dirpath, fn), "score": score})
            if len(hits) >= max_hits:
                break
        return hits

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        query = params.get("query", "").lower()
        # "email folder" should match a directory named "email" — the filler words
        # would otherwise drag every fuzzy score down.
        for noise in (" folder", " file", " directory"):
            query = query.replace(noise, "")
        query = query.replace("my ", "").strip()
        location = params.get("location")

        results = []
        searched = []
        if location:
            root = self._resolve_location(location)
            searched.append(root)
            results = self._live_walk(query, root)
        else:
            conn = get_db()
            files = conn.execute("SELECT path, name, mtime FROM file_index").fetchall()
            searched.append("indexed folders (Desktop, Documents, Downloads, Pictures)")
            if files:
                choices = [f["name"] for f in files]
                for match in process.extract(query, choices, scorer=fuzz.partial_ratio, limit=5):
                    if match[1] >= 70:
                        for f in files:
                            if f["name"] == match[0]:
                                results.append({"name": match[0], "path": f["path"], "score": match[1]})
                                break
            if not results:
                # Index miss — fall back to a live walk of the whole home directory
                root = str(Path.home())
                searched.append(root)
                results = self._live_walk(query, root)

        if not results:
            return {"error": f"No files matching '{query}' found in {', '.join(searched)}. Try a different name or tell me a specific folder to search."}
        results.sort(key=lambda r: r["score"], reverse=True)
        return {"success": True, "matches": results[:5], "searched": searched}

class OpenFileTool(Tool):
    name: str = "open_file"
    description: str = "Opens a file or folder using its absolute path."
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The absolute path of the file/folder to open"}
        },
        "required": ["path"]
    }
    output_schema: Dict[str, Any] = {"type": "object"}
    permission_level: str = "safe"

    KNOWN_FOLDERS: ClassVar[set] = {"desktop", "documents", "downloads", "pictures", "music", "videos"}

    def _resolve(self, path: str) -> str:
        home = Path.home()
        p = path.strip().strip('"')
        # Bare folder name like "desktop" / "my documents"
        key = p.lower().replace("my ", "").rstrip("\\/")
        if key in self.KNOWN_FOLDERS:
            return str(home / key.capitalize())
        p = os.path.expandvars(os.path.expanduser(p))
        # Model sometimes invents a wrong user dir (C:/Users/YourUser/...) — remap onto the real home
        norm = p.replace("/", "\\")
        parts = norm.split("\\")
        if len(parts) >= 3 and parts[0].lower() == "c:" and parts[1].lower() == "users" and not norm.lower().startswith(str(home).lower()):
            candidate = str(home / "\\".join(parts[3:])) if len(parts) > 3 else str(home)
            if os.path.exists(candidate):
                return candidate
        return p

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        path = self._resolve(params.get("path", ""))
        if not os.path.exists(path):
            return {"error": f"Path does not exist: {path}"}
            
        try:
            os.startfile(path)
            if os.path.isdir(path):
                folder_name = os.path.basename(path.rstrip("\\/")) or path
                threading.Thread(target=bring_explorer_folder_to_front, args=(folder_name,), daemon=True).start()
            else:
                launched_at = time.time()
                hint = os.path.splitext(os.path.basename(path))[0]
                bring_app_to_front(hint, launched_at)
                remember_target(find_existing_window(hint))
            return {"success": True, "path": path}
        except Exception as e:
            return {"error": str(e)}

registry.register(OpenAppTool())
registry.register(CloseAppTool())
registry.register(SearchFileTool())
registry.register(OpenFileTool())
