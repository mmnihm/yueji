"""Favorites + folder update notifications for Yueji."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

logger = logging.getLogger("yueji.favorites")


async def ensure_favorites_schema(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_favorites (
                user_id INTEGER NOT NULL,
                source_folder_id TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, source_folder_id)
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_fav_folder ON user_favorites(source_folder_id)"
        )
        # source_folders may already exist; add columns if missing
        async with db.execute("PRAGMA table_info(source_folders)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        if cols:
            if "updated_at" not in cols:
                await db.execute(
                    "ALTER TABLE source_folders ADD COLUMN updated_at TEXT"
                )
            if "update_note" not in cols:
                await db.execute(
                    "ALTER TABLE source_folders ADD COLUMN update_note TEXT"
                )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS folder_update_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_folder_id TEXT NOT NULL,
                note TEXT,
                notified_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        await db.commit()


async def is_favorited(db_path: str, user_id: int, folder_id: str) -> bool:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT 1 FROM user_favorites WHERE user_id=? AND source_folder_id=?",
            (int(user_id), str(folder_id)),
        ) as cur:
            return await cur.fetchone() is not None


async def add_favorite(db_path: str, user_id: int, folder_id: str) -> bool:
    """Return True if newly added, False if already existed."""
    await ensure_favorites_schema(db_path)
    async with aiosqlite.connect(db_path) as db:
        try:
            await db.execute(
                """
                INSERT INTO user_favorites (user_id, source_folder_id, created_at)
                VALUES (?, ?, datetime('now'))
                """,
                (int(user_id), str(folder_id)),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def remove_favorite(db_path: str, user_id: int, folder_id: str) -> bool:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "DELETE FROM user_favorites WHERE user_id=? AND source_folder_id=?",
            (int(user_id), str(folder_id)),
        )
        await db.commit()
        return cur.rowcount > 0


async def toggle_favorite(db_path: str, user_id: int, folder_id: str) -> tuple[bool, str]:
    """Returns (now_favorited, message)."""
    await ensure_favorites_schema(db_path)
    if await is_favorited(db_path, user_id, folder_id):
        await remove_favorite(db_path, user_id, folder_id)
        return False, "已取消收藏"
    await add_favorite(db_path, user_id, folder_id)
    return True, "已收藏，有更新时会提醒你"


async def list_user_favorites(
    db_path: str, user_id: int, *, limit: int = 20, offset: int = 0
) -> list[tuple]:
    """Rows: (folder_id, name, item_count, updated_at)."""
    await ensure_favorites_schema(db_path)
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            """
            SELECT f.source_folder_id,
                   COALESCE(s.name, f.source_folder_id),
                   COALESCE(s.item_count, 0),
                   s.updated_at
            FROM user_favorites f
            LEFT JOIN source_folders s ON s.source_folder_id = f.source_folder_id
            WHERE f.user_id = ?
            ORDER BY COALESCE(s.updated_at, f.created_at) DESC
            LIMIT ? OFFSET ?
            """,
            (int(user_id), int(limit), int(offset)),
        ) as cur:
            return await cur.fetchall()


async def count_user_favorites(db_path: str, user_id: int) -> int:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM user_favorites WHERE user_id=?",
            (int(user_id),),
        ) as cur:
            row = await cur.fetchone()
            return int(row[0] or 0) if row else 0


async def list_latest_updates(
    db_path: str, *, limit: int = 10
) -> list[tuple]:
    """Rows: (folder_id, name, updated_at, update_note, item_count)."""
    await ensure_favorites_schema(db_path)
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            """
            SELECT source_folder_id, name, updated_at, update_note, item_count
            FROM source_folders
            WHERE updated_at IS NOT NULL AND updated_at != ''
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ) as cur:
            return await cur.fetchall()


async def get_folder_name(db_path: str, folder_id: str) -> str:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT name FROM source_folders WHERE source_folder_id=?",
            (str(folder_id),),
        ) as cur:
            row = await cur.fetchone()
            return (row[0] if row and row[0] else str(folder_id))[:80]


async def mark_folder_updated(
    db_path: str, folder_id: str, note: str = ""
) -> int:
    """Set updated_at and return number of favoriting users."""
    await ensure_favorites_schema(db_path)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    note = (note or "")[:200]
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            UPDATE source_folders
            SET updated_at=?, update_note=?
            WHERE source_folder_id=?
            """,
            (now, note, str(folder_id)),
        )
        async with db.execute(
            "SELECT user_id FROM user_favorites WHERE source_folder_id=?",
            (str(folder_id),),
        ) as cur:
            users = [int(r[0]) for r in await cur.fetchall()]
        await db.execute(
            """
            INSERT INTO folder_update_log (source_folder_id, note, notified_count)
            VALUES (?, ?, ?)
            """,
            (str(folder_id), note, len(users)),
        )
        await db.commit()
    return len(users)


async def list_favoriters(db_path: str, folder_id: str) -> list[int]:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT user_id FROM user_favorites WHERE source_folder_id=?",
            (str(folder_id),),
        ) as cur:
            return [int(r[0]) for r in await cur.fetchall()]


async def notify_favoriters(
    bot,
    db_path: str,
    folder_id: str,
    *,
    note: str = "",
    max_notify: int = 500,
) -> tuple[int, int]:
    """Send update notices. Returns (ok, fail)."""
    name = await get_folder_name(db_path, folder_id)
    users = await list_favoriters(db_path, folder_id)
    users = users[: max(0, int(max_notify))]
    note_line = f"\n备注：{note}" if note else ""
    text = (
        f"⭐ 你收藏的分组有更新\n"
        f"━━━━━━━━━━━━━━\n"
        f"📁 {name}\n"
        f"{note_line}\n\n"
        f"点击下方按钮打开该分组。"
    )
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📂 打开分组",
                    callback_data=f"source_open:{folder_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⭐ 我的收藏",
                    callback_data="fav_list:0",
                )
            ],
        ]
    )
    ok = fail = 0
    for uid in users:
        try:
            await bot.send_message(uid, text, reply_markup=kb)
            ok += 1
        except Exception as exc:
            fail += 1
            logger.warning("notify user=%s folder=%s err=%s", uid, folder_id, str(exc)[:120])
    return ok, fail


async def format_latest_updates_block(db_path: str, *, limit: int = 5) -> str:
    rows = await list_latest_updates(db_path, limit=limit)
    if not rows:
        return ""
    lines = ["📌 最新更新："]
    for fid, name, updated_at, note, _cnt in rows:
        title = (name or fid or "")[:36]
        ts = (updated_at or "")[:16]
        extra = f" · {(note or '')[:20]}" if note else ""
        lines.append(f"· {title}（{ts}）{extra}")
    return "\n".join(lines)
