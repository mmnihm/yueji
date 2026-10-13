#!/usr/bin/env python3
"""Avoid always picking the same random folder: prefer ORDER BY RANDOM()."""
from pathlib import Path
import re

def main():
    path = Path(__file__).resolve().parent / "bot.py"
    text = path.read_text(encoding="utf-8")
    orig = text
    replacements = [
        (
            r"FROM source_folders\s+ORDER BY source_folder_id\s+LIMIT 1",
            "FROM source_folders ORDER BY RANDOM() LIMIT 1",
        ),
        (
            r"FROM source_folders\s+ORDER BY name\s+LIMIT 1",
            "FROM source_folders ORDER BY RANDOM() LIMIT 1",
        ),
        (
            r"FROM source_folders\s+LIMIT 1",
            "FROM source_folders ORDER BY RANDOM() LIMIT 1",
        ),
    ]
    for a, b in replacements:
        text2, n = re.subn(a, b, text, flags=re.I)
        if n:
            print(f"patched x{n}: {a[:50]}")
            text = text2
    if "choose_random_source_folder" in text and "ORDER BY RANDOM()" not in text:
        print("WARN: choose_random_source_folder has no ORDER BY RANDOM()")
        print("请把选文件夹 SQL 改成 ORDER BY RANDOM() LIMIT ?")
    if text == orig:
        print("no SQL pattern changed. Check:")
        print('  sqlite3 yueji.db "SELECT COUNT(*) FROM source_folders;"')
    else:
        compile(text, "bot.py", "exec")
        path.write_text(text, encoding="utf-8")
        print("OK: random diversity patch applied")

if __name__ == "__main__":
    main()
