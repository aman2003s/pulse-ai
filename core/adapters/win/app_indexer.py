import os
import json
import subprocess
from pathlib import Path
from core.db import get_db

# Common short/alternate forms people actually say, mapped onto the app's real
# Get-StartApps display name (lowercased). Keeps the DB clean while still letting
# "open calc" or "open word" resolve without relying on fuzzy-match luck.
_EXTRA_ALIASES = {
    "calculator": ["calc"],
    "google chrome": ["chrome"],
    "microsoft edge": ["edge"],
    "word": ["microsoft word", "ms word"],
    "excel": ["microsoft excel", "ms excel"],
    "windows powershell": ["powershell"],
    "command prompt": ["cmd", "command line"],
    "snipping tool": ["snip", "screenshot tool"],
    "settings": ["windows settings", "system settings"],
}


def _scan_lnk_shortcuts():
    """Name -> real .lnk path, for every classic Start Menu shortcut. A real .lnk is
    always a reliable launch target (os.startfile just opens the shortcut, exactly
    like double-clicking it) — unlike shell:AppsFolder, which only reliably resolves
    genuine UWP/MSIX packages (confirmed via testing: it silently no-ops for several
    real installed apps — Discord, Antigravity — that register a taskbar AppUserModelID
    without being an actual Package, even though os.startfile() reports no error)."""
    lnk_map = {}
    for base in (
        Path(os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs")),
        Path(os.path.expandvars(r"%AppData%\Microsoft\Windows\Start Menu\Programs")),
    ):
        if not base.exists():
            continue
        for p in base.rglob("*.lnk"):
            key = p.stem.lower()
            if key not in lnk_map:
                lnk_map[key] = str(p)
    return lnk_map


def build_app_index():
    """Enumerates every app Windows itself would show in the Start Menu via
    Get-StartApps (for the full, canonical list of names — including UWP/MSIX-
    packaged apps like Notepad, Calculator, Paint, Photos, Snipping Tool, Settings,
    which have NO .lnk shortcut at all and were previously invisible to open_app).

    Launch path per app, in priority order:
      1. A real .lnk shortcut with a matching name, if one exists — always reliable,
         this is literally what the Start Menu tile itself points to.
      2. `shell:AppsFolder\\<AppID>` otherwise — only reachable here for genuine
         packaged apps with no traditional shortcut, which is exactly the case this
         resolves correctly (verified: Notepad/Calculator/Paint/Photos/Settings all
         launch correctly this way). For apps with a plain taskbar-only AppID and NO
         real package (Discord, Antigravity, etc.), the .lnk match above wins instead,
         since shell:AppsFolder was confirmed to silently fail for those.
    """
    print("Building App Index (Get-StartApps + .lnk shortcuts)...")
    lnk_map = _scan_lnk_shortcuts()

    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        raw = json.loads(proc.stdout) if proc.stdout.strip() else []
    except Exception as e:
        print(f"App Index: Get-StartApps failed ({e}), falling back to .lnk-only index.")
        raw = []

    if isinstance(raw, dict):  # PowerShell unwraps a single-item array to an object
        raw = [raw]

    apps = {}
    for entry in raw:
        name = (entry.get("Name") or "").strip()
        app_id = (entry.get("AppID") or "").strip()
        if not name or not app_id:
            continue
        key = name.lower()
        if key in apps:
            continue
        path = lnk_map.get(key) or f"shell:AppsFolder\\{app_id}"
        aliases = [key] + _EXTRA_ALIASES.get(key, [])
        apps[key] = {"path": path, "aliases": aliases}

    # Anything found only via .lnk scanning (Get-StartApps came back empty, or missed
    # a shortcut for some reason) — don't regress coverage below the old indexer.
    for key, path in lnk_map.items():
        if key not in apps:
            apps[key] = {"path": path, "aliases": [key] + _EXTRA_ALIASES.get(key, [])}

    conn = get_db()
    with conn:
        conn.execute("DELETE FROM app_index")
        for name, data in apps.items():
            conn.execute(
                "INSERT INTO app_index (name, path, aliases) VALUES (?, ?, ?)",
                (name, data["path"], json.dumps(data["aliases"]))
            )
    conn.close()
    print(f"App Index built with {len(apps)} apps ({sum(1 for a in apps.values() if a['path'].startswith('shell:'))} via shell:AppsFolder, rest via .lnk).")


if __name__ == "__main__":
    build_app_index()
