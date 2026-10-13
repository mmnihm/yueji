#!/usr/bin/env python3
"""Assemble switchable-source files from _blob_*.b64 next to this script."""
from pathlib import Path
import base64

ROOT = Path(__file__).resolve().parent

def main():
    parts = sorted(ROOT.glob("_blob_svc_*.b64"), key=lambda p: int(p.stem.split("_")[-1]))
    if not parts:
        raise SystemExit("missing _blob_svc_*.b64")
    svc = base64.b64decode("".join(p.read_text() for p in parts))
    ui = base64.b64decode((ROOT / "_blob_ui.b64").read_text())
    (ROOT / "forum_import_service.py").write_bytes(svc)
    (ROOT / "bot_forum_ui.py").write_bytes(ui)
    sc = ROOT / "source_config.py"
    if not sc.exists():
        raise SystemExit("missing source_config.py — pull from branch first")
    for n in ("forum_import_service.py", "bot_forum_ui.py", "source_config.py"):
        compile((ROOT / n).read_text(encoding="utf-8"), n, "exec")
        print("OK", n, (ROOT / n).stat().st_size)
    print("Install complete. pm2 restart yueji after register_forum_ui in bot.py")

if __name__ == "__main__":
    main()
