#!/usr/bin/env python3
"""Apply random-preview UX fix to bot.py (reads random_preview_snippet.py)."""
from pathlib import Path
import re

def main():
    root = Path(__file__).resolve().parent
    new = (root / "random_preview_snippet.py").read_text(encoding="utf-8")
    path = root / "bot.py"
    text = path.read_text(encoding="utf-8")
    start = text.find("async def send_source_messages(")
    if start < 0:
        raise SystemExit("bot.py 中找不到 async def send_source_messages(")
    # end: next top-level async def after send_source_folder_card, or choose_random_source_folder
    end = text.find("async def choose_random_source_folder")
    if end < 0:
        # find send_source_folder_card then next async def at column 0
        rest = text[start + 10:]
        m = re.search(r"\nasync def (?!send_source_messages|send_source_folder_card)", rest)
        if not m:
            raise SystemExit("bot.py 中找不到替换结束位置")
        end = start + 10 + m.start() + 1
    text = text[:start] + new.rstrip() + "\n\n\n" + text[end:]
    compile(text, "bot.py", "exec")
    path.write_text(text, encoding="utf-8")
    print("OK: patched send_source_messages + send_source_folder_card")
    print("请 pm2 restart yueji 后测试：媒体下方应有一条带按钮的文字")

if __name__ == "__main__":
    main()
