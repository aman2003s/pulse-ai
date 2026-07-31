"""Force a just-opened window to the foreground.

Windows blocks background processes from stealing focus by default (anti-annoyance
protection), so a plain os.startfile() often leaves the new window sitting behind
whatever you were already looking at, or just flashing in the taskbar if minimized.
Pulse is exactly the case this restriction targets — the user asked for the window,
but the request came from a background process — so we deliberately bypass it via
the AttachThreadInput trick, the same technique window managers use.
"""
import time
import ctypes
import psutil
import win32gui
import win32con
import win32process

# The window Pulse last intentionally opened/switched to. Screen-reading and
# input tools check this before acting: if the user clicked elsewhere, a
# notification stole focus, or a launch race left some other window in front
# (confirmed live: read_screen right after open_app once read Pulse's own
# coding-session window instead of the app it had just opened), we forcibly
# refocus the intended target and continue rather than silently reading or
# acting on whatever happens to be in front.
_LAST_TARGET_HWND = None


def remember_target(hwnd: int):
    global _LAST_TARGET_HWND
    _LAST_TARGET_HWND = hwnd


def get_remembered_target():
    """The last window Pulse intentionally opened/switched to, if it still
    exists — used for "close it"/"close the app" with no name given, where the
    real OS foreground window isn't reliable: a TYPED command means the user
    just clicked into Pulse's own UI, so raw foreground would be Pulse's own
    window, not the app they mean. Returns None if nothing remembered yet or
    the window has since closed."""
    if _LAST_TARGET_HWND and win32gui.IsWindow(_LAST_TARGET_HWND):
        return _LAST_TARGET_HWND
    return None


def ensure_target_focused():
    """If we have a remembered target window and it's not the one currently in
    focus, force it back to front. Safe to call unconditionally — a no-op if
    there's no target yet, the target closed, or it's already focused.
    Verifies the switch actually landed and retries briefly if not — belt and
    braces on top of _force_foreground's own WM_NULL sync, since a genuinely
    hung/slow-starting window's queue could still outlast that call's timeout."""
    hwnd = _LAST_TARGET_HWND
    if not hwnd or not win32gui.IsWindow(hwnd):
        return
    for _ in range(3):
        if win32gui.GetForegroundWindow() == hwnd:
            return
        _force_foreground(hwnd)
        time.sleep(0.1)


def _force_foreground(hwnd: int):
    # Researched (2026-07-27): SetForegroundWindow is ASYNCHRONOUS — it posts a
    # message to the target's queue and returns immediately, before that window
    # has actually processed it (Raymond Chen / "The Old New Thing", confirming
    # this exact race). GetForegroundWindow() called right after can legitimately
    # still return the OLD window if the target's queue hasn't caught up yet —
    # confirmed live: read_screen right after open_app read Pulse's own window
    # instead of the app just opened, well after open_app's own synchronous
    # foregrounding call had already returned. The documented fix: send a
    # blocking WM_NULL via SendMessageTimeout, which can't complete until the
    # target's queue has processed everything ahead of it, including the
    # foreground-switch request — only then is GetForegroundWindow() reliable.
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        fg_hwnd = win32gui.GetForegroundWindow()
        fg_thread = win32process.GetWindowThreadProcessId(fg_hwnd)[0]
        target_thread = win32process.GetWindowThreadProcessId(hwnd)[0]
        user32 = ctypes.windll.user32
        if fg_thread != target_thread:
            user32.AttachThreadInput(fg_thread, target_thread, True)
            try:
                win32gui.SetForegroundWindow(hwnd)
                win32gui.BringWindowToTop(hwnd)
            finally:
                user32.AttachThreadInput(fg_thread, target_thread, False)
        else:
            win32gui.SetForegroundWindow(hwnd)
        win32gui.SendMessageTimeout(hwnd, win32con.WM_NULL, 0, 0, win32con.SMTO_ABORTIFHUNG, 2000)
    except Exception:
        pass


def bring_process_to_front(pid: int, timeout_s: float = 3.0) -> bool:
    """Poll for a visible top-level window belonging to `pid` (or a child spawned
    from it, e.g. a launcher process handing off to the real app) and foreground it."""
    deadline = time.time() + timeout_s
    target_pids = {pid}
    try:
        target_pids |= {c.pid for c in psutil.Process(pid).children(recursive=True)}
    except Exception:
        pass

    while time.time() < deadline:
        found = []

        def cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd) or not win32gui.GetWindowText(hwnd):
                return
            _, wpid = win32process.GetWindowThreadProcessId(hwnd)
            if wpid in target_pids:
                found.append(hwnd)

        win32gui.EnumWindows(cb, None)
        if found:
            _force_foreground(found[0])
            return True
        time.sleep(0.15)
    return False


def _title_matches(title_lower: str, needle: str) -> bool:
    """Word-boundary match for window TITLES specifically — titles are
    arbitrary user content (file names, page titles), where a plain substring
    check produces real false positives: confirmed live, searching for "word"
    (to open Microsoft Word) matched an existing Notepad window titled
    something like "password.txt - Notepad", foregrounding the wrong app
    entirely instead of ever launching Word. Requires the needle to appear as
    its own token, not embedded inside a longer word. Process names are still
    matched by plain substring separately (unaffected) — those are short,
    vendor-controlled strings ("winword.exe") where this ambiguity doesn't
    arise the same way, and a boundary check would incorrectly reject that
    legitimate match."""
    import re
    return re.search(r'(?<![a-z0-9])' + re.escape(needle) + r'(?![a-z0-9])', title_lower) is not None


def _find_windows(name_hint: str, started_after: float = None):
    """Enumerates visible top-level windows whose owning process name or title
    matches name_hint. If started_after is given, only windows from processes
    created at/after that time count (used for 'wait for the NEW window from
    this launch'); if None, ANY matching window counts (used for 'is this app
    already running at all')."""
    needle = name_hint.lower()
    found = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd) or not win32gui.GetWindowText(hwnd):
            return
        _, wpid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            proc = psutil.Process(wpid)
            if started_after is not None and proc.create_time() < started_after - 1:  # clock-skew buffer
                return
            if needle in proc.name().lower() or _title_matches(win32gui.GetWindowText(hwnd).lower(), needle):
                found.append(hwnd)
        except Exception:
            pass

    win32gui.EnumWindows(cb, None)
    return found


def find_existing_window(name_hint: str):
    """Synchronous check: is there ALREADY a visible window for this app, right
    now? Used to decide reuse-vs-launch BEFORE calling os.startfile at all —
    confirmed real bug otherwise: repeatedly asking to 'open notepad' kept
    spawning new windows/tabs instead of reusing the one already open. Returns
    the window handle, or None."""
    found = _find_windows(name_hint)
    return found[0] if found else None


def bring_app_to_front(name_hint: str, started_after: float, timeout_s: float = 6.0) -> bool:
    """os.startfile() gives us no PID (it's fire-and-forget ShellExecute), so match by
    process start time + a loose name hint instead of an exact PID."""
    # Most single-instance apps (Chrome, Discord, etc.) don't spawn a new process
    # when already running — os.startfile just re-activates the existing window,
    # which the old "only accept windows newer than this launch" check unconditionally
    # ignored, so an already-open app was NEVER brought forward: it just polled
    # uselessly for the full timeout and gave up. Checking for an existing window
    # FIRST catches this instantly, no polling needed.
    existing = find_existing_window(name_hint)
    if existing:
        _force_foreground(existing)
        return True

    # Genuinely new launch — poll for its window to appear, up to timeout_s.
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        found = _find_windows(name_hint, started_after=started_after)
        if found:
            _force_foreground(found[0])
            return True
        time.sleep(0.15)
    return False


def bring_explorer_folder_to_front(folder_name: str, timeout_s: float = 3.0) -> bool:
    """Explorer windows aren't owned by a fresh process (they run inside a shared
    explorer.exe host), so we match by window class + title instead of PID."""
    deadline = time.time() + timeout_s
    needle = folder_name.lower()

    while time.time() < deadline:
        found = []

        def cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            if win32gui.GetClassName(hwnd) != "CabinetWClass":
                return
            title = win32gui.GetWindowText(hwnd)
            if needle in title.lower():
                found.append(hwnd)

        win32gui.EnumWindows(cb, None)
        if found:
            _force_foreground(found[0])
            return True
        time.sleep(0.15)
    return False
