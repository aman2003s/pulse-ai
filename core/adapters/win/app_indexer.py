import os
import sqlite3
import json
from pathlib import Path
from core.db import get_db

def build_app_index():
    print("Building App Index...")
    apps = {}
    
    # Common Start Menu locations
    paths = [
        Path(os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs")),
        Path(os.path.expandvars(r"%AppData%\Microsoft\Windows\Start Menu\Programs"))
    ]
    
    for path in paths:
        if not path.exists():
            continue
        for p in path.rglob("*.lnk"):
            name = p.stem.lower()
            if name not in apps:
                apps[name] = {
                    "path": str(p),
                    "aliases": [name]
                }
                # Add some common aliases for specific apps
                if "chrome" in name:
                    apps[name]["aliases"].append("google chrome")
                elif "edge" in name:
                    apps[name]["aliases"].append("microsoft edge")
                elif "word" in name:
                    apps[name]["aliases"].append("microsoft word")
                elif "excel" in name:
                    apps[name]["aliases"].append("microsoft excel")
    
    conn = get_db()
    with conn:
        conn.execute("DELETE FROM app_index")
        for name, data in apps.items():
            conn.execute(
                "INSERT INTO app_index (name, path, aliases) VALUES (?, ?, ?)",
                (name, data["path"], json.dumps(data["aliases"]))
            )
    conn.close()
    print(f"App Index built with {len(apps)} apps.")

if __name__ == "__main__":
    build_app_index()
