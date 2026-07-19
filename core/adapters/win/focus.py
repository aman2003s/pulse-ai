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


def _force_foreground(hwnd: int):
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


def bring_app_to_front(name_hint: str, started_after: float, timeout_s: float = 3.0) -> bool:
    """os.startfile() gives us no PID (it's fire-and-forget ShellExecute), so match by
    process start time + a loose name hint instead of an exact PID."""
    deadline = time.time() + timeout_s
    needle = name_hint.lower()

    while time.time() < deadline:
        found = []

        def cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd) or not win32gui.GetWindowText(hwnd):
                return
            _, wpid = win32process.GetWindowThreadProcessId(hwnd)
            try:
                proc = psutil.Process(wpid)
                if proc.create_time() < started_after - 1:  # small buffer for clock skew
                    return
                if needle in proc.name().lower() or needle in win32gui.GetWindowText(hwnd).lower():
                    found.append(hwnd)
            except Exception:
                pass

        win32gui.EnumWindows(cb, None)
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
