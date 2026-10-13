#!/usr/bin/env python3
"""Apply random-preview UX fix to bot.py (reads random_preview_snippet.py)."""
from pathlib import Path

def main():
    root = Path(__file__).resolve().parent
    new = (root / "random_preview_snippet.py").read_text(encoding="utf-8")
    path = root / "bot.py"
    text = path.read_text(encoding="utf-8")
    start = text.find("async def send_source_messages(")
    end = text.find("async def choose_random_source_folder")
    if start < 0 or end < 0:
        raise SystemExit("bot.py 中找不到目标函数")
    text = text[:start] + new.rstrip() + "\n\n\n" + text[end:]
    compile(text, "bot.py", "exec")
    path.write_text(text, encoding="utf-8")
    print("OK: 随机资源 = 标题+数量 + 最多3个预览文件")

if __name__ == "__main__":
    main()
