"""Source chat ID: DB config first, then env. No hardcoded production repo required."""
from __future__ import annotations
import os
from pathlib import Path
import aiosqlite

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("YUEJI_DB", str(ROOT / "yueji.db")))
_ENV_DEFAULT = os.getenv("FORUM_CHAT_ID", "").strip()


async def get_source_chat_id(db_path=None) -> int:
    path = Path(db_path) if db_path else DB_PATH
    try:
        async with aiosqlite.connect(path) as db:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)"
            )
            await db.commit()
            async with db.execute(
                "SELECT value FROM config WHERE key IN ('forum_chat_id','source_repo_id') "
                "ORDER BY CASE key WHEN 'forum_chat_id' THEN 0 ELSE 1 END"
            ) as cur:
                for (val,) in await cur.fetchall():
                    s = str(val or "").strip()
                    if s.lstrip("-").isdigit():
                        return int(s)
    except Exception:
        pass
    if _ENV_DEFAULT and _ENV_DEFAULT.lstrip("-").isdigit():
        return int(_ENV_DEFAULT)
    raise RuntimeError(
        "未配置源仓库。请在机器人后台「更换源仓库 ID」设置，或在 .env 写 FORUM_CHAT_ID。"
    )


async def set_source_chat_id(db_path, chat_id: int) -> None:
    path = Path(db_path) if db_path else DB_PATH
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)"
        )
        cid = str(int(chat_id))
        for k in ("forum_chat_id", "source_repo_id"):
            await db.execute(
                "INSERT OR REPLACE INTO config(key,value) VALUES(?,?)", (k, cid)
            )
        await db.execute(
            "INSERT OR REPLACE INTO config(key,value) VALUES('source_provider','telegram_forum')"
        )
        await db.commit()
