#!/usr/bin/env python3
"""Run on VPS /root/yueji: make source chat ID configurable via config table."""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
svc = ROOT / "forum_import_service.py"
if not svc.exists():
    sys.exit("forum_import_service.py not found")

text = svc.read_text(encoding="utf-8")
if "async def get_source_chat_id" in text:
    print("Already patched: get_source_chat_id exists")
else:
    m = re.search(r'FORUM_CHAT_ID\s*=\s*int\(os\.getenv\("FORUM_CHAT_ID"[^)]*\)\)', text)
    if not m:
        sys.exit("Cannot find FORUM_CHAT_ID assignment")
    inject = '''
_DEFAULT_FORUM_CHAT_ID = int(os.getenv("FORUM_CHAT_ID", "-1002332481220"))


async def get_source_chat_id(db_path=None):
    path = Path(db_path) if db_path else DB_PATH
    try:
        async with aiosqlite.connect(path) as db:
            await db.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")
            await db.commit()
            async with db.execute(
                "SELECT value FROM config WHERE key IN ('forum_chat_id','source_repo_id') "
                "ORDER BY CASE key WHEN 'forum_chat_id' THEN 0 ELSE 1 END"
            ) as cur:
                for (val,) in await cur.fetchall():
                    if val and str(val).strip().lstrip("-").isdigit():
                        return int(str(val).strip())
    except Exception:
        pass
    return int(_DEFAULT_FORUM_CHAT_ID)


async def set_source_chat_id(db_path, chat_id: int):
    path = Path(db_path) if db_path else DB_PATH
    async with aiosqlite.connect(path) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")
        for k in ("forum_chat_id", "source_repo_id"):
            await db.execute(
                "INSERT OR REPLACE INTO config(key,value) VALUES(?,?)",
                (k, str(int(chat_id))),
            )
        await db.execute(
            "INSERT OR REPLACE INTO config(key,value) VALUES('source_provider','telegram_forum')"
        )
        await db.commit()


FORUM_CHAT_ID = _DEFAULT_FORUM_CHAT_ID
'''
    text = text[: m.start()] + inject + text[m.end() :]
    anchor = "api_id, api_hash = load_api_creds()\n    path = Path(db_path) if db_path else DB_PATH"
    if anchor in text and "source_chat_id = await get_source_chat_id" not in text:
        text = text.replace(
            anchor,
            anchor + "\n    source_chat_id = await get_source_chat_id(path)",
            1,
        )
    text = text.replace("get_entity(FORUM_CHAT_ID)", "get_entity(source_chat_id)")
    text = text.replace("str(FORUM_CHAT_ID)", "str(source_chat_id)")
    compile(text, "forum_import_service.py", "exec")
    svc.write_text(text, encoding="utf-8")
    print("OK: forum_import_service.py patched")

print(
    """
换源并扫描:

  # 把 NEWID 换成新群/频道/论坛 ID
  sqlite3 yueji.db "INSERT OR REPLACE INTO config(key,value) VALUES('forum_chat_id','NEWID');"
  sqlite3 yueji.db "INSERT OR REPLACE INTO config(key,value) VALUES('source_repo_id','NEWID');"

  # 或: 编辑 .env 里 FORUM_CHAT_ID=NEWID 后 pm2 restart yueji

  # 再扫描（后台按钮或）:
  python import_forum.py --apply

  sqlite3 yueji.db "SELECT COUNT(*) FROM source_folders;"
  sqlite3 yueji.db "SELECT chat_id, COUNT(*) FROM source_resources GROUP BY chat_id;"

悦己 Bot 必须在该源有读权限，否则预览仍会 message not found。
"""
)
