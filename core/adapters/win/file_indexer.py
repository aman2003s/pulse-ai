import os
import sqlite3
from pathlib import Path
from core.db import get_db

def is_hidden(filepath):
    # Basic hidden check for Windows
    return filepath.name.startswith('.') or 'AppData' in filepath.parts or 'node_modules' in filepath.parts

def build_file_index():
    print("Building File Index...")
    user_profile = Path(os.path.expanduser("~"))
    target_dirs = ["Documents", "Desktop", "Downloads", "Pictures"]
    
    conn = get_db()
    
    with conn:
        conn.execute("DELETE FROM file_index")
        count = 0
        
        for d in target_dirs:
            target_path = user_profile / d
            if not target_path.exists():
                continue
                
            for root, dirs, files in os.walk(target_path):
                root_path = Path(root)
                # Skip hidden directories
                dirs[:] = [d for d in dirs if not is_hidden(root_path / d)]
                
                for file in files:
                    file_path = root_path / file
                    if is_hidden(file_path):
                        continue
                        
                    try:
                        mtime = file_path.stat().st_mtime
                        conn.execute(
                            "INSERT OR IGNORE INTO file_index (path, name, mtime) VALUES (?, ?, ?)",
                            (str(file_path), file.lower(), mtime)
                        )
                        count += 1
                        if count >= 100000: # Cap at 100k
                            print("Reached 100k files cap.")
                            return
                    except Exception:
                        pass
                        
    print(f"File Index built with {count} files.")

if __name__ == "__main__":
    build_file_index()
