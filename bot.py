from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
DB_PATH = os.getenv("YUEJI_DB", str(ROOT / "yueji.db"))
BOT: Bot | None = None
dp = Dispatcher()
log = logging.getLogger("yueji")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

DEFAULTS = {
    "browse_minutes": "5",
    "browse_scope": "all",
    "browse_admin_exempt": "1",
    "random_count": "3",
    "daily_unlock_limit": "0",
    "daily_video_limit": "0",
    "unlock_price": "10",
    "checkin_reward": "3",
    "initial_points": "10",
    "menu_show_random": "1",
    "menu_order": "my_info,checkin,random,buy,help",
    "menu_my_info_text": "👤 我的信息",
    "menu_checkin_text": "🎁 每日签到",
    "menu_random_text": "🎲 随机资源",
    "menu_buy_text": "💎 积分兑换",
    "menu_help_text": "❓ 使用帮助",
    "welcome_text": "欢迎使用悦己分享机器人。请选择下方菜单开始使用。",
    "help_text": "使用菜单浏览资源。解锁后点击「开始浏览」，按批次发送，避免重复点击。",
    "announcement_text": "",
    "customer_service_url": "",
    "source_provider": "telegram_forum",
    "source_repo_id": "",
    "buy_options": "10,30,50,100",
}
MENU_KEYS = ("my_info", "checkin", "random", "buy", "help")
MENU_CONFIG = {
    "my_info": "menu_my_info_text",
    "checkin": "menu_checkin_text",
    "random": "menu_random_text",
    "buy": "menu_buy_text",
    "help": "menu_help_text",
}
SEARCH_WAITING: set[int] = set()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_admin(user_id: int) -> bool:
    return bool(ADMIN_ID and int(user_id) == ADMIN_ID)


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
            points INTEGER NOT NULL DEFAULT 0, total_spent INTEGER NOT NULL DEFAULT 0,
            unlocked_count INTEGER NOT NULL DEFAULT 0, banned INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')), last_seen TEXT
        );
        CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS checkins (
            user_id INTEGER NOT NULL, checkin_date TEXT NOT NULL, reward INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(user_id, checkin_date)
        );
        CREATE TABLE IF NOT EXISTS points_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL, reason TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS redemption_codes (
            code TEXT PRIMARY KEY, points INTEGER NOT NULL, used_by INTEGER,
            used_at TEXT, created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS source_folders (
            source_folder_id TEXT PRIMARY KEY, name TEXT NOT NULL,
            item_count INTEGER NOT NULL DEFAULT 0, image_count INTEGER NOT NULL DEFAULT 0,
            video_count INTEGER NOT NULL DEFAULT 0, file_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS source_resources (
            resource_key TEXT PRIMARY KEY, folder_id TEXT NOT NULL, chat_id TEXT NOT NULL,
            message_id INTEGER NOT NULL, title TEXT DEFAULT '', file_type TEXT DEFAULT '',
            file_id TEXT DEFAULT '', data TEXT DEFAULT '', grouped_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_source_resources_folder ON source_resources(folder_id, message_id);
        CREATE TABLE IF NOT EXISTS source_folder_unlocks (
            user_id INTEGER NOT NULL, source_folder_id TEXT NOT NULL,
            unlocked_at TEXT DEFAULT (datetime('now')), PRIMARY KEY(user_id, source_folder_id)
        );
        CREATE TABLE IF NOT EXISTS source_browse_sessions (
            user_id INTEGER NOT NULL, source_folder_id TEXT NOT NULL,
            started_at TEXT NOT NULL, expires_at TEXT,
            PRIMARY KEY(user_id, source_folder_id)
        );
        CREATE TABLE IF NOT EXISTS favorites (
            user_id INTEGER NOT NULL, source_folder_id TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')), PRIMARY KEY(user_id, source_folder_id)
        );
        CREATE TABLE IF NOT EXISTS browse_usage (
            user_id INTEGER NOT NULL, usage_date TEXT NOT NULL, video_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(user_id, usage_date)
        );
        CREATE TABLE IF NOT EXISTS memberships (
            user_id INTEGER PRIMARY KEY, expires_at TEXT, granted_by INTEGER,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        """)
        # Safe additive migrations for databases created by older versions.
        async with db.execute("PRAGMA table_info(users)") as cur:
            user_cols = {r[1] for r in await cur.fetchall()}
        for col, definition in (("banned", "INTEGER NOT NULL DEFAULT 0"), ("first_name", "TEXT"), ("last_seen", "TEXT")):
            if col not in user_cols:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
        async with db.execute("PRAGMA table_info(source_resources)") as cur:
            resource_cols = {r[1] for r in await cur.fetchall()}
        for col, definition in (("grouped_id", "TEXT"), ("data", "TEXT DEFAULT ''"), ("file_id", "TEXT DEFAULT ''"), ("title", "TEXT DEFAULT ''"), ("file_type", "TEXT DEFAULT ''")):
            if col not in resource_cols:
                await db.execute(f"ALTER TABLE source_resources ADD COLUMN {col} {definition}")
        async with db.execute("PRAGMA table_info(source_folders)") as cur:
            folder_cols = {r[1] for r in await cur.fetchall()}
        for col in ("item_count", "image_count", "video_count", "file_count"):
            if col not in folder_cols:
                await db.execute(f"ALTER TABLE source_folders ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
        for key, value in DEFAULTS.items():
            await db.execute("INSERT OR IGNORE INTO config(key,value) VALUES(?,?)", (key, value))
        await db.commit()


async def get_config(key: str, default: str = "") -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM config WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
    return str(row[0]) if row else DEFAULTS.get(key, default)


async def set_config(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        await db.commit()


async def ensure_user(user_id: int, username: str | None = None, first_name: str | None = None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users(user_id,username,first_name,points,last_seen)
            VALUES(?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
              first_name=excluded.first_name,last_seen=excluded.last_seen
        """, (int(user_id), username or "", first_name or "", int(await get_config("initial_points", "10")), now_iso()))
        await db.commit()


async def user_is_banned(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT banned FROM users WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
    return bool(row and row[0])


def menu_label(key: str) -> str:
    return DEFAULTS[MENU_CONFIG[key]]


async def reply_menu() -> ReplyKeyboardMarkup:
    order_raw = await get_config("menu_order", DEFAULTS["menu_order"])
    order = [k.strip() for k in order_raw.split(",") if k.strip() in MENU_KEYS]
    order = list(dict.fromkeys(order)) or list(MENU_KEYS)
    if (await get_config("menu_show_random", "1")) != "1":
        order = [k for k in order if k != "random"]
    labels = {k: await get_config(MENU_CONFIG[k], DEFAULTS[MENU_CONFIG[k]]) for k in MENU_KEYS}
    buttons = [KeyboardButton(text=labels[k]) for k in order]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


async def user_points(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT points FROM users WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


async def add_points(user_id: int, amount: int, reason: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute("INSERT OR IGNORE INTO users(user_id,points) VALUES(?,0)", (user_id,))
        await db.execute("UPDATE users SET points=MAX(0,points+?) WHERE user_id=?", (amount, user_id))
        await db.execute("INSERT INTO points_log(user_id,amount,reason) VALUES(?,?,?)", (user_id, amount, reason))
        await db.commit()


async def validate_config_value(key: str, raw: str):
    value = (raw or "").strip()
    if key in ("browse_minutes",):
        try:
            n = int(value)
            if n < 0 or n > 1440:
                raise ValueError
            return str(n), None
        except ValueError:
            return None, "请输入 0–1440 的整数。"
    if key in ("daily_unlock_limit", "daily_video_limit", "unlock_price", "checkin_reward", "initial_points", "random_count"):
        try:
            n = int(value)
            limit = 1000 if key == "daily_unlock_limit" else 100000 if key in ("unlock_price", "initial_points") else 10000
            if n < 0 or n > limit or (key == "random_count" and not 1 <= n <= 20):
                raise ValueError
            return str(n), None
        except ValueError:
            return None, "请输入范围内的非负整数。"
    if key == "browse_scope":
        return (value, None) if value in ("all", "videos") else (None, "只能是 all 或 videos。")
    if key == "browse_admin_exempt":
        return (value, None) if value in ("0", "1") else (None, "只能是 0 或 1。")
    if key == "buy_options":
        try:
            nums = [int(x.strip()) for x in value.split(",") if x.strip()]
            if not nums or len(nums) > 10 or any(n <= 0 or n > 100000 for n in nums) or len(set(nums)) != len(nums):
                raise ValueError
            return ",".join(map(str, nums)), None
        except ValueError:
            return None, "请填写不重复的正整数，例如 10,30,50。"
    if key == "menu_order":
        items = [x.strip() for x in value.split(",") if x.strip()]
        if not items or any(x not in MENU_KEYS for x in items) or len(items) != len(set(items)):
            return None, "菜单顺序只能使用 my_info,checkin,random,buy,help，不能重复。"
        return ",".join(items), None
    if key in MENU_CONFIG.values():
        if not value or len(value) > 32 or any(value == DEFAULTS[MENU_CONFIG[k]] for k in MENU_KEYS if MENU_CONFIG[k] != key):
            return None, "菜单名称不能为空、不能超过 32 字，也不能与其他菜单重名。"
        return value, None
    if key in ("welcome_text", "help_text", "announcement_text", "customer_service_url"):
        if len(value) > 3500:
            return None, "内容不能超过 3500 字。"
        if key == "customer_service_url" and value and not value.startswith(("https://", "http://", "https://t.me/")):
            return None, "客服链接必须以 http:// 或 https:// 开头。"
        return value, None
    return None, "这个设置项不支持修改。"


def resource_page(rows, offset: int, limit: int = 10):
    """Return a page of messages without splitting a Telegram album."""
    if offset < 0 or offset >= len(rows):
        return []
    end = min(offset + limit, len(rows))
    if end < len(rows) and end > offset:
        last_group = rows[end - 1][3] if len(rows[end - 1]) > 3 else None
        if last_group:
            while end < len(rows) and len(rows[end]) > 3 and rows[end][3] == last_group:
                end += 1
    return rows[offset:end]


async def create_browse_session(user_id: int, fid: str):
    minutes = max(0, int(await get_config("browse_minutes", "5") or 0))
    exempt = (await get_config("browse_admin_exempt", "1")) == "1" and is_admin(user_id)
    started = datetime.now(timezone.utc)
    expires = None if minutes == 0 or exempt else started + timedelta(minutes=minutes)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO source_browse_sessions(user_id,source_folder_id,started_at,expires_at)
            VALUES(?,?,?,?) ON CONFLICT(user_id,source_folder_id) DO NOTHING
        """, (user_id, fid, started.isoformat(), expires.isoformat() if expires else None))
        await db.commit()
        async with db.execute("SELECT started_at,expires_at FROM source_browse_sessions WHERE user_id=? AND source_folder_id=?", (user_id, fid)) as cur:
            row = await cur.fetchone()
    return row


async def browse_session_expired(user_id: int, fid: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT expires_at FROM source_browse_sessions WHERE user_id=? AND source_folder_id=?", (user_id, fid)) as cur:
            row = await cur.fetchone()
    if not row or not row[0]:
        return False
    try:
        expires = datetime.fromisoformat(row[0])
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= expires
    except (TypeError, ValueError):
        return False


async def get_import_status_summary() -> str:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='import_status'") as cur:
                if not await cur.fetchone():
                    return "尚无导入记录"
            async with db.execute("SELECT status,topics_imported,topics_skipped,topics_failed,resources_new,video_count,photo_count FROM import_status WHERE id=1") as cur:
                row = await cur.fetchone()
        if not row:
            return "尚无导入记录"
        return f"{row[0]}：话题成功 {row[1]}，跳过 {row[2]}，失败 {row[3]}；新增资源 {row[4]}，视频 {row[5]}，照片 {row[6]}"
    except Exception as exc:
        return f"读取导入状态失败：{str(exc)[:100]}"


async def sync_source_repository():
    provider = await get_config("source_provider", "telegram_forum")
    if provider == "telegram_forum":
        raise RuntimeError("阻止本次旧仓库同步：当前资源来源为 Telegram 论坛，禁止旧 MySQL 同步覆盖论坛索引。")
    raise RuntimeError("当前版本未启用旧 MySQL 仓库同步，请使用管理员的论坛扫描导入功能。")


def folder_keyboard(fid: str, unlocked: bool, favorite: bool, price: int) -> InlineKeyboardMarkup:
    rows = []
    if unlocked:
        rows.append([InlineKeyboardButton(text="▶️ 开始浏览资源", callback_data=f"source_browse_start:{fid}")])
    else:
        rows.append([InlineKeyboardButton(text=f"🔓 解锁（{price}积分）", callback_data=f"source_unlock:{fid}")])
    rows.append([
        InlineKeyboardButton(text="⭐ 已收藏" if favorite else "⭐ 收藏", callback_data=f"fav_toggle:{fid}"),
        InlineKeyboardButton(text="🎲 再随机", callback_data="source_random_again"),
    ])
    rows.append([InlineKeyboardButton(text="📁 返回文件夹列表", callback_data="source_folders_page:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def fetch_random_folder_resource(fid: str):
    """Pick one real indexed media message to preview and display its stored title/topic."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT title,data,chat_id,message_id,file_type,grouped_id
               FROM source_resources WHERE folder_id=? ORDER BY RANDOM() LIMIT 1""",
            (fid,),
        ) as cur:
            return await cur.fetchone()


def resource_labels(resource_info, folder_name: str):
    """Read topic/title only from stored index metadata; never invent resource titles."""
    title, raw_data = (resource_info[0], resource_info[1]) if resource_info else ("", "")
    metadata = {}
    try:
        value = json.loads(raw_data or "{}")
        if isinstance(value, dict):
            metadata = value
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    topic = str(metadata.get("topic_title") or folder_name or "未分类").strip()
    caption = str(metadata.get("caption") or "").strip()
    resource_title = str(title or caption or folder_name or "未命名资源").strip()
    resource_title = " ".join(resource_title.split())[:220]
    topic = " ".join(topic.split())[:120]
    return topic or "未分类", resource_title or str(folder_name or "未命名资源")


async def send_random_resource_preview(chat_id: int, fid: str, resource_info) -> int:
    """Send one actual file or its entire album group; never split a selected album."""
    if BOT is None:
        raise RuntimeError("机器人尚未初始化")
    if not resource_info:
        raise RuntimeError("这个文件夹没有可预览的资源")
    _, _, source_chat, message_id, _, grouped_id = resource_info
    source_chat = str(source_chat)
    if grouped_id:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """SELECT message_id FROM source_resources
                   WHERE folder_id=? AND chat_id=? AND grouped_id=?
                   ORDER BY message_id""",
                (fid, source_chat, str(grouped_id)),
            ) as cur:
                message_ids = [int(row[0]) for row in await cur.fetchall()]
        if not message_ids:
            message_ids = [int(message_id)]
        if len(message_ids) > 1:
            sent = await BOT.copy_messages(
                chat_id=chat_id, from_chat_id=source_chat,
                message_ids=message_ids, protect_content=True,
            )
            return len(sent or [])
        await BOT.copy_message(
            chat_id=chat_id, from_chat_id=source_chat,
            message_id=int(message_ids[0]), protect_content=True,
        )
        return 1
    await BOT.copy_message(
        chat_id=chat_id, from_chat_id=source_chat,
        message_id=int(message_id), protect_content=True,
    )
    return 1


def folder_keyboard(fid: str, unlocked: bool, favorite: bool, price: int) -> InlineKeyboardMarkup:
    rows = []
    if unlocked:
        rows.append([InlineKeyboardButton(text="▶️ 开始浏览资源", callback_data=f"source_browse_start:{fid}")])
    else:
        rows.append([InlineKeyboardButton(text=f"🔓 解锁（{price}积分）", callback_data=f"source_unlock:{fid}")])
    rows.append([
        InlineKeyboardButton(text="⭐ 已收藏" if favorite else "⭐ 收藏", callback_data=f"fav_toggle:{fid}"),
        InlineKeyboardButton(text="🎲 再随机", callback_data="source_random_again"),
    ])
    rows.append([InlineKeyboardButton(text="📁 返回文件夹列表", callback_data="source_folders_page:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def build_folder_card(fid: str, user_id: int, resource_info=None):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT source_folder_id,name,item_count,image_count,video_count,file_count FROM source_folders WHERE source_folder_id=?", (fid,)) as cur:
            folder = await cur.fetchone()
        if not folder:
            return None
        async with db.execute("SELECT 1 FROM source_folder_unlocks WHERE user_id=? AND source_folder_id=?", (user_id, fid)) as cur:
            unlocked = bool(await cur.fetchone())
        async with db.execute("SELECT 1 FROM favorites WHERE user_id=? AND source_folder_id=?", (user_id, fid)) as cur:
            favorite = bool(await cur.fetchone())
        async with db.execute("SELECT expires_at FROM memberships WHERE user_id=?", (user_id,)) as cur:
            membership = await cur.fetchone()
        member_active = bool(membership and (membership[0] is None or membership[0] > now_iso()))
        async with db.execute("SELECT file_type,grouped_id FROM source_resources WHERE folder_id=?", (fid,)) as cur:
            kinds = await cur.fetchall()
    fid, name, count, images, videos, files = folder
    if resource_info is None:
        resource_info = await fetch_random_folder_resource(str(fid))
    topic_title, resource_title = resource_labels(resource_info, str(name))
    # Count album groups, not individual photo messages.
    album_ids = {g for kind, g in kinds if g}
    if kinds:
        count = len(kinds)
        images = sum(1 for kind, _ in kinds if kind in ("photo", "image"))
        videos = sum(1 for kind, _ in kinds if kind in ("video", "animation"))
        files = sum(1 for kind, _ in kinds if kind not in ("photo", "image", "video", "animation"))
    price = 0 if member_active else max(0, int(await get_config("unlock_price", "10") or 0))
    text = (
        f"📁 {name}\n━━━━━━━━━━━━━━\n"
        f"🏷️ 话题名称：{topic_title}\n"
        f"📝 随机资源标题：{resource_title}\n"
        f"🎬 视频消息：{videos} 个\n"
        f"🖼️ 相册组：{len(album_ids)} 组\n"
        f"📄 其他文件：{files} 个\n"
        f"📦 资源记录：{count} 条\n"
        f"🔐 状态：{'已解锁' if unlocked else ('会员免费解锁' if member_active else f'需要 {price} 积分解锁')}\n"
        "👇 点击下方按钮继续"
    )
    return text, folder_keyboard(str(fid), unlocked, favorite, price)


async def send_source_folder_card(message: Message, folder, user_id: int | None = None):
    uid = user_id if user_id is not None else message.from_user.id
    fid = str(folder[0])
    resource_info = await fetch_random_folder_resource(fid)
    preview_ok = False
    preview_error = ""
    if resource_info:
        try:
            await send_random_resource_preview(message.chat.id, fid, resource_info)
            preview_ok = True
        except Exception as exc:
            preview_error = str(exc)[:140]
            log.warning("随机预览发送失败 folder=%s: %s", fid, preview_error)
    card = await build_folder_card(fid, uid, resource_info=resource_info)
    if not card:
        await message.answer("找不到这个文件夹，可能已被移除。")
        return False
    text, keyboard = card
    if not resource_info:
        text += "\n⚠️ 这个文件夹暂时没有可预览的资源。"
    elif not preview_ok:
        text += f"\n⚠️ 预览文件发送失败，请点击按钮解锁后浏览。\n原因：{preview_error}"
    await message.answer(text, reply_markup=keyboard)
    return True


async def choose_random_source_folder(message: Message, user_id: int | None = None):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT source_folder_id,name,item_count,image_count,video_count,file_count
               FROM source_folders WHERE item_count>0 ORDER BY RANDOM() LIMIT 3"""
        ) as cur:
            folders = await cur.fetchall()
    if not folders:
        await message.answer("资源仓库还没有文件夹，请管理员先导入资源。")
        return False
    await message.answer(f"🎲 本次为你随机挑选 {len(folders)} 个不同文件夹，先看真实文件预览，再选择解锁或浏览。")
    for folder in folders:
        await send_source_folder_card(message, folder, user_id=user_id)
    return True


async def list_source_folders(message: Message, page: int = 0, edit: CallbackQuery | None = None):
    page = max(0, page)
    page_size = 8
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM source_folders WHERE item_count>0") as cur:
            total = int((await cur.fetchone())[0] or 0)
        async with db.execute("SELECT source_folder_id,name,item_count, image_count,video_count,file_count FROM source_folders WHERE item_count>0 ORDER BY name LIMIT ? OFFSET ?", (page_size, page * page_size)) as cur:
            rows = await cur.fetchall()
    if not rows:
        text = "📭 暂无可浏览的资源文件夹。"
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 随机推荐", callback_data="source_random_again")]])
    else:
        buttons = []
        for fid, name, count, images, videos, files in rows:
            buttons.append([InlineKeyboardButton(text=f"📁 {str(name)[:38]} · {count}", callback_data=f"source_folder:{fid}")])
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️ 上一页", callback_data=f"source_folders_page:{page-1}"))
        if (page + 1) * page_size < total:
            nav.append(InlineKeyboardButton(text="下一页 ➡️", callback_data=f"source_folders_page:{page+1}"))
        if nav:
            buttons.append(nav)
        buttons.append([InlineKeyboardButton(text="🆕 最新资源文件夹", callback_data="source_latest")])
        buttons.append([InlineKeyboardButton(text="🎲 随机推荐", callback_data="source_random_again")])
        text = f"📚 资源文件夹（第 {page+1}/{max(1,(total+page_size-1)//page_size)} 页）\n点击文件夹查看详情、解锁和浏览。"
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    if edit:
        try:
            await edit.message.edit_text(text, reply_markup=kb)
        except Exception:
            await edit.message.answer(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


class PartialSendError(RuntimeError):
    def __init__(self, sent: int, message: str):
        super().__init__(message)
        self.sent = sent


async def send_source_messages(chat_id: int, rows, preview: bool = False, preview_limit: int = 3):
    """Copy source messages without unsafe whole-batch retry; preserve albums."""
    if BOT is None:
        raise RuntimeError("机器人尚未初始化")
    cleaned = []
    seen = set()
    for row in rows:
        if not row or not row[0] or not row[1]:
            continue
        item = (str(row[0]), int(row[1]), str(row[2] or "") if len(row) > 2 else "", str(row[3]) if len(row) > 3 and row[3] else None)
        if item[:2] not in seen:
            seen.add(item[:2])
            cleaned.append(item)
    if preview:
        cleaned = cleaned[:max(1, min(10, int(preview_limit or 3)))]
    sent = 0
    index = 0
    try:
        while index < len(cleaned):
            source_chat, mid, kind, grouped = cleaned[index]
            if grouped:
                group_rows = []
                j = index
                while j < len(cleaned) and cleaned[j][0] == source_chat and cleaned[j][3] == grouped:
                    group_rows.append(cleaned[j])
                    j += 1
                ids = [r[1] for r in group_rows]
                if len(ids) == 1:
                    await BOT.copy_message(chat_id=chat_id, from_chat_id=source_chat, message_id=ids[0], protect_content=True)
                else:
                    await BOT.copy_messages(chat_id=chat_id, from_chat_id=source_chat, message_ids=ids, protect_content=True)
                sent += len(ids)
                index = j
            else:
                await BOT.copy_message(chat_id=chat_id, from_chat_id=source_chat, message_id=mid, protect_content=True)
                sent += 1
                index += 1
    except Exception as exc:
        raise PartialSendError(sent, str(exc)[:220]) from exc
    return sent


async def send_browse_page_summary(message: Message, fid: str, rows, offset: int, sent: int, skipped_video_batch: bool = False):
    page = resource_page(rows, offset)
    next_offset = min(offset + len(page), len(rows))
    remaining = max(0, len(rows) - next_offset)
    note = ("⏱️ 浏览时限已结束，本批包含视频，已跳过该批。" if skipped_video_batch else f"📦 本批已发送 {sent} 条资源。")
    if remaining:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"📦 继续发送（剩余 {remaining} 条）", callback_data=f"source_more:{fid}:{next_offset}")
        ]])
        await message.answer(f"{note}\n剩余 {remaining} 条，点击下方继续。", reply_markup=kb)
    else:
        await message.answer(f"{note}\n✅ 该文件夹已到最后一批。")


async def apply_video_quota(user_id: int, rows):
    """Return a quota-safe page without splitting album groups, plus video message count."""
    limit = max(0, int(await get_config("daily_video_limit", "0") or 0))
    if limit == 0 or is_admin(user_id):
        return list(rows), sum(1 for r in rows if str(r[2] or "") in ("video", "animation"))
    today = datetime.now().date().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT video_count FROM browse_usage WHERE user_id=? AND usage_date=?", (user_id, today)) as cur:
            row = await cur.fetchone()
    remaining = max(0, limit - (int(row[0]) if row else 0))
    allowed, i, videos_used = [], 0, 0
    while i < len(rows):
        row = rows[i]
        group = row[3] if len(row) > 3 else None
        chunk = [row]
        j = i + 1
        if group:
            while j < len(rows) and len(rows[j]) > 3 and rows[j][3] == group:
                chunk.append(rows[j])
                j += 1
        video_count = sum(1 for item in chunk if str(item[2] or "") in ("video", "animation"))
        if video_count == 0 or videos_used + video_count <= remaining:
            allowed.extend(chunk)
            videos_used += video_count
        i = j if group else i + 1
    return allowed, videos_used


async def record_video_usage(user_id: int, count: int) -> None:
    if count <= 0:
        return
    today = datetime.now().date().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO browse_usage(user_id,usage_date,video_count) VALUES(?,?,?)
            ON CONFLICT(user_id,usage_date) DO UPDATE SET video_count=video_count+excluded.video_count
        """, (user_id, today, count))
        await db.commit()


async def fetch_folder_resources(fid: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT chat_id,message_id,file_type,grouped_id FROM source_resources WHERE folder_id=? ORDER BY message_id", (fid,)) as cur:
            return await cur.fetchall()


async def daily_unlock_count(user_id: int) -> int:
    today = datetime.now().date().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM source_folder_unlocks WHERE user_id=? AND date(unlocked_at)=?", (user_id, today)) as cur:
            return int((await cur.fetchone())[0] or 0)


async def show_user_info(message: Message):
    uid = message.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT points,total_spent,unlocked_count,created_at FROM users WHERE user_id=?", (uid,)) as cur:
            row = await cur.fetchone()
        async with db.execute("SELECT COUNT(*) FROM favorites WHERE user_id=?", (uid,)) as cur:
            favorites = int((await cur.fetchone())[0] or 0)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT expires_at FROM memberships WHERE user_id=?", (uid,)) as cur:
            member = await cur.fetchone()
    member_text = "永久会员" if member and member[0] is None else (f"会员至 {member[0][:10]}" if member and member[0] and member[0] > now_iso() else "普通用户")
    await message.answer(
        f"👤 我的信息\n━━━━━━━━━━━━━━\n🆔 ID：{uid}\n💎 积分：{row[0] if row else 0}\n"
        f"🔓 已解锁：{row[2] if row else 0} 个文件夹\n⭐ 收藏：{favorites} 个\n🎟️ 会员：{member_text}\n"
        f"📅 注册时间：{(row[3] or '')[:10] if row else '—'}",
        reply_markup=await reply_menu(),
    )


async def handle_checkin(message: Message):
    uid = message.from_user.id
    today = datetime.now().date().isoformat()
    reward = max(0, int(await get_config("checkin_reward", "3") or 0))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute("SELECT reward FROM checkins WHERE user_id=? AND checkin_date=?", (uid, today)) as cur:
            exists = await cur.fetchone()
        if exists:
            await db.rollback()
            await message.answer("✅ 今天已经签到过了，明天再来吧。")
            return
        await db.execute("INSERT INTO checkins(user_id,checkin_date,reward) VALUES(?,?,?)", (uid, today, reward))
        await db.execute("UPDATE users SET points=points+? WHERE user_id=?", (reward, uid))
        await db.execute("INSERT INTO points_log(user_id,amount,reason) VALUES(?,?,?)", (uid, reward, "每日签到"))
        await db.commit()
    await message.answer(f"🎉 签到成功，获得 {reward} 积分！\n当前积分：{await user_points(uid)}")


async def show_buy_options(message: Message):
    raw = await get_config("buy_options", "10,30,50,100")
    options = [int(x) for x in raw.split(",") if x.strip().isdigit()]
    buttons = [[InlineKeyboardButton(text=f"兑换 {n} 积分", callback_data=f"buy_points:{n}")] for n in options]
    buttons.append([InlineKeyboardButton(text="🎟️ 使用兑换码", callback_data="redeem_prompt")])
    await message.answer("💎 积分中心\n\n当前支持管理员发放兑换码。选择积分档位查看兑换说明，暂未接入在线支付。", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


async def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ 浏览与解锁设置", callback_data="admin_browse_settings")],
        [InlineKeyboardButton(text="🧩 用户菜单设置", callback_data="admin_menu_settings")],
        [InlineKeyboardButton(text="📚 资源文件夹", callback_data="source_folders_page:0")],
        [InlineKeyboardButton(text="📡 论坛扫描导入", callback_data="forum_scan_menu")],
        [InlineKeyboardButton(text="📊 资源/用户统计", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 用户与会员管理", callback_data="admin_users_menu")],
        [InlineKeyboardButton(text="🎟️ 生成兑换码", callback_data="admin_code_prompt")],
        [InlineKeyboardButton(text="💾 备份数据库", callback_data="admin_backup")],
    ])


async def settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱️ 浏览时限", callback_data="admin_set:browse_minutes"),
         InlineKeyboardButton(text="🔓 解锁价格", callback_data="admin_set:unlock_price")],
        [InlineKeyboardButton(text="📅 每日解锁上限", callback_data="admin_set:daily_unlock_limit"),
         InlineKeyboardButton(text="🎬 每日视频上限", callback_data="admin_set:daily_video_limit")],
        [InlineKeyboardButton(text="🛡️ 管理员免时限", callback_data="admin_toggle:browse_admin_exempt"),
         InlineKeyboardButton(text="⏳ 到期限制范围", callback_data="admin_toggle:browse_scope")],
        [InlineKeyboardButton(text="🎁 签到奖励", callback_data="admin_set:checkin_reward"),
         InlineKeyboardButton(text="💎 初始积分", callback_data="admin_set:initial_points")],
        [InlineKeyboardButton(text="↩️ 返回管理中心", callback_data="admin_home")],
    ])


async def menu_settings_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for key in MENU_KEYS:
        label = await get_config(MENU_CONFIG[key], DEFAULTS[MENU_CONFIG[key]])
        rows.append([InlineKeyboardButton(text=f"✏️ {label}", callback_data=f"admin_set:{MENU_CONFIG[key]}")])
    rows.extend([
        [InlineKeyboardButton(text="🔀 菜单顺序", callback_data="admin_set:menu_order"),
         InlineKeyboardButton(text="🎲 随机按钮显示/隐藏", callback_data="admin_toggle:menu_show_random")],
        [InlineKeyboardButton(text="📝 欢迎文案", callback_data="admin_set:welcome_text"),
         InlineKeyboardButton(text="❓ 帮助文案", callback_data="admin_set:help_text")],
        [InlineKeyboardButton(text="📣 公告文案", callback_data="admin_set:announcement_text"),
         InlineKeyboardButton(text="🧑‍💻 客服链接", callback_data="admin_set:customer_service_url")],
        [InlineKeyboardButton(text="↩️ 返回管理中心", callback_data="admin_home")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(CommandStart())
async def start(message: Message):
    await ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    if await user_is_banned(message.from_user.id):
        await message.answer("账号暂时不可用，请联系管理员。")
        return
    welcome = await get_config("welcome_text", DEFAULTS["welcome_text"])
    announcement = await get_config("announcement_text", "")
    await message.answer(welcome + (f"\n\n📣 {announcement}" if announcement else ""), reply_markup=await reply_menu())
    service_url = await get_config("customer_service_url", "")
    if service_url:
        await message.answer("🧑‍💻 客服入口", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="联系在线客服", url=service_url)]
        ]))


@dp.message(Command("help"))
async def help_command(message: Message):
    await message.answer(await get_config("help_text", DEFAULTS["help_text"]), reply_markup=await reply_menu())


@dp.message(Command("admin"))
async def admin_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("无权限。")
        return
    await message.answer("🛠️ 悦己管理中心", reply_markup=await admin_keyboard())


@dp.message(Command("folders"))
async def folders_command(message: Message):
    await list_source_folders(message, 0)


@dp.message(Command("search"))
async def search_command(message: Message):
    SEARCH_WAITING.add(message.from_user.id)
    await message.answer("请输入要搜索的文件夹名称或关键词。")


@dp.callback_query(F.data == "source_random_again")
async def source_random_again(callback: CallbackQuery):
    if await user_is_banned(callback.from_user.id):
        await callback.answer("账号不可用", show_alert=True)
        return
    await callback.answer("正在随机推荐")
    await choose_random_source_folder(callback.message, user_id=callback.from_user.id)


@dp.callback_query(F.data.startswith("source_folders_page:"))
async def source_folders_page(callback: CallbackQuery):
    try:
        page = max(0, int(callback.data.split(":", 1)[1]))
    except ValueError:
        page = 0
    await callback.answer()
    await list_source_folders(callback.message, page, edit=callback)


@dp.callback_query(F.data == "source_latest")
async def source_latest(callback: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT f.source_folder_id,f.name,f.item_count
            FROM source_folders f LEFT JOIN source_resources r ON r.folder_id=f.source_folder_id
            WHERE f.item_count>0 GROUP BY f.source_folder_id
            ORDER BY MAX(r.message_id) DESC LIMIT 10
        """) as cur:
            rows = await cur.fetchall()
    if not rows:
        await callback.answer("暂无资源", show_alert=True)
        return
    buttons = [[InlineKeyboardButton(text=f"🆕 {str(r[1])[:38]} · {r[2]}", callback_data=f"source_folder:{r[0]}")] for r in rows]
    buttons.append([InlineKeyboardButton(text="📁 全部文件夹", callback_data="source_folders_page:0")])
    await callback.answer()
    try:
        await callback.message.edit_text("🆕 最新资源文件夹", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    except Exception:
        await callback.message.answer("🆕 最新资源文件夹", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.callback_query(F.data.startswith("source_folder:"))
async def source_folder_detail(callback: CallbackQuery):
    fid = callback.data.split(":", 1)[1]
    card = await build_folder_card(fid, callback.from_user.id)
    if not card:
        await callback.answer("文件夹不存在", show_alert=True)
        return
    await callback.answer()
    try:
        await callback.message.edit_text(card[0], reply_markup=card[1])
    except Exception:
        await callback.message.answer(card[0], reply_markup=card[1])


@dp.callback_query(F.data.startswith("fav_toggle:"))
async def fav_toggle(callback: CallbackQuery):
    fid = callback.data.split(":", 1)[1]
    uid = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM favorites WHERE user_id=? AND source_folder_id=?", (uid, fid)) as cur:
            exists = await cur.fetchone()
        if exists:
            await db.execute("DELETE FROM favorites WHERE user_id=? AND source_folder_id=?", (uid, fid))
            text = "已取消收藏"
        else:
            await db.execute("INSERT OR IGNORE INTO favorites(user_id,source_folder_id) VALUES(?,?)", (uid, fid))
            text = "已收藏"
        await db.commit()
    card = await build_folder_card(fid, uid)
    await callback.answer(text)
    if card:
        try:
            await callback.message.edit_text(card[0], reply_markup=card[1])
        except Exception:
            await callback.message.answer(card[0], reply_markup=card[1])


@dp.callback_query(F.data.startswith("source_unlock:"))
async def source_unlock(callback: CallbackQuery):
    uid = callback.from_user.id
    fid = callback.data.split(":", 1)[1]
    if await user_is_banned(uid):
        await callback.answer("账号不可用", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute("SELECT 1 FROM source_folders WHERE source_folder_id=?", (fid,)) as cur:
            folder = await cur.fetchone()
        async with db.execute("SELECT 1 FROM source_folder_unlocks WHERE user_id=? AND source_folder_id=?", (uid, fid)) as cur:
            unlocked = await cur.fetchone()
        if not folder:
            await db.rollback()
            await callback.answer("文件夹不存在", show_alert=True)
            return
        if unlocked:
            await db.rollback()
            await callback.answer("这个文件夹已经解锁")
        else:
            limit = max(0, int(await get_config("daily_unlock_limit", "0") or 0))
            async with db.execute("SELECT expires_at FROM memberships WHERE user_id=?", (uid,)) as cur:
                member_row = await cur.fetchone()
            member_active = bool(member_row and (member_row[0] is None or member_row[0] > now_iso()))
            price = 0 if member_active else max(0, int(await get_config("unlock_price", "10") or 0))
            if limit and not member_active:
                async with db.execute("SELECT COUNT(*) FROM source_folder_unlocks WHERE user_id=? AND date(unlocked_at)=date('now')", (uid,)) as cur:
                    today_count = int((await cur.fetchone())[0] or 0)
                if today_count >= limit:
                    await db.rollback()
                    await callback.answer(f"今天最多解锁 {limit} 个文件夹", show_alert=True)
                    return
            async with db.execute("SELECT points FROM users WHERE user_id=?", (uid,)) as cur:
                row = await cur.fetchone()
            points = int(row[0]) if row else 0
            if points < price:
                await db.rollback()
                await callback.answer(f"积分不足，需要 {price} 积分，你当前有 {points} 积分", show_alert=True)
                return
            await db.execute("UPDATE users SET points=points-?,total_spent=total_spent+?,unlocked_count=unlocked_count+1 WHERE user_id=?", (price, price, uid))
            await db.execute("INSERT INTO source_folder_unlocks(user_id,source_folder_id) VALUES(?,?)", (uid, fid))
            await db.execute("INSERT INTO points_log(user_id,amount,reason) VALUES(?,?,?)", (uid, -price, f"解锁文件夹 {fid}"))
            await db.commit()
            await callback.answer("解锁成功")
    card = await build_folder_card(fid, uid)
    if card:
        try:
            await callback.message.edit_text(card[0], reply_markup=card[1])
        except Exception:
            await callback.message.answer(card[0], reply_markup=card[1])


@dp.callback_query(F.data.startswith("source_browse_start:"))
async def source_browse_start(callback: CallbackQuery):
    fid = callback.data.split(":", 1)[1]
    uid = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM source_folder_unlocks WHERE user_id=? AND source_folder_id=?", (uid, fid)) as cur:
            unlocked = await cur.fetchone()
    if not unlocked:
        await callback.answer("请先解锁这个文件夹", show_alert=True)
        return
    rows = await fetch_folder_resources(fid)
    if not rows:
        await callback.answer("该文件夹没有可发送的资源", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM source_browse_sessions WHERE user_id=? AND source_folder_id=?", (uid, fid)) as cur:
            existing = await cur.fetchone()
    if existing:
        if await browse_session_expired(uid, fid):
            await callback.answer("浏览时限已结束，不能重新开始", show_alert=True)
        else:
            await callback.answer("浏览已开始，请使用继续发送按钮", show_alert=True)
        return
    session = await create_browse_session(uid, fid)
    minutes = max(0, int(await get_config("browse_minutes", "5") or 0))
    exempt = (await get_config("browse_admin_exempt", "1")) == "1" and is_admin(uid)
    time_text = "不限时" if minutes == 0 or exempt else f"{minutes} 分钟"
    await callback.answer("开始浏览")
    try:
        await callback.message.edit_text(f"▶️ 已开始浏览\n浏览时限：{time_text}\n已发送的内容不会在计时结束后撤回。")
    except Exception:
        await callback.message.answer(f"▶️ 已开始浏览\n浏览时限：{time_text}")
    page = resource_page(rows, 0)
    page, video_count = await apply_video_quota(uid, page)
    if not page:
        await send_browse_page_summary(callback.message, fid, rows, 0, 0, skipped_video_batch=True)
        return
    try:
        sent = await send_source_messages(callback.message.chat.id, page)
        await record_video_usage(uid, video_count)
        await send_browse_page_summary(callback.message, fid, rows, 0, sent)
    except PartialSendError as exc:
        await callback.message.answer(f"⚠️ 发送过程发生错误，已确认成功发送 {exc.sent} 条。为防止重复，机器人没有自动重发。请先检查已收到的文件后再联系管理员。\n错误：{str(exc)[:160]}")


@dp.callback_query(F.data.startswith("source_more:"))
async def source_folder_more(callback: CallbackQuery):
    try:
        _, fid, offset_text = callback.data.split(":", 2)
        offset = max(0, int(offset_text))
    except (ValueError, TypeError):
        await callback.answer("分页参数无效", show_alert=True)
        return
    uid = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM source_folder_unlocks WHERE user_id=? AND source_folder_id=?", (uid, fid)) as cur:
            unlocked = await cur.fetchone()
    if not unlocked:
        await callback.answer("请先解锁", show_alert=True)
        return
    rows = await fetch_folder_resources(fid)
    if offset >= len(rows):
        await callback.answer("资源已发送完", show_alert=True)
        return
    if not await browse_session_exists(uid, fid):
        await callback.answer("请先点击开始浏览", show_alert=True)
        return
    expired = await browse_session_expired(uid, fid)
    scope = await get_config("browse_scope", "all")
    page = resource_page(rows, offset)
    contains_video = any(str(r[2] or "") in ("video", "animation") for r in page)
    if expired and scope == "all":
        await callback.answer("浏览时限已结束", show_alert=True)
        return
    await callback.answer("正在发送")
    if expired and scope == "videos" and contains_video:
        await send_browse_page_summary(callback.message, fid, rows, offset, 0, skipped_video_batch=True)
        return
    page, video_count = await apply_video_quota(uid, page)
    if not page:
        await send_browse_page_summary(callback.message, fid, rows, offset, 0, skipped_video_batch=True)
        return
    try:
        sent = await send_source_messages(callback.message.chat.id, page)
        await record_video_usage(uid, video_count)
        await send_browse_page_summary(callback.message, fid, rows, offset, sent)
    except PartialSendError as exc:
        await callback.message.answer(f"⚠️ 发送中断，已确认发送 {exc.sent} 条。为避免重复未自动重试，请先检查上方已收到的文件。\n错误：{str(exc)[:160]}")


async def browse_session_exists(uid: int, fid: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM source_browse_sessions WHERE user_id=? AND source_folder_id=?", (uid, fid)) as cur:
            return bool(await cur.fetchone())


@dp.callback_query(F.data.startswith("buy_points:"))
async def buy_points_info(callback: CallbackQuery):
    try:
        points = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer("参数无效", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(f"💎 你选择了 {points} 积分档位。当前版本未接入在线支付，请联系管理员获取兑换码。")


@dp.callback_query(F.data == "redeem_prompt")
async def redeem_prompt(callback: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (f"pending_redeem:{callback.from_user.id}", "1"))
        await db.commit()
    await callback.answer("请发送兑换码", show_alert=True)
    await callback.message.answer("请直接发送兑换码，或发送：兑换码 ABC123")


@dp.callback_query(F.data == "admin_home")
async def admin_home(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text("🛠️ 悦己管理中心", reply_markup=await admin_keyboard())


@dp.callback_query(F.data == "admin_browse_settings")
async def admin_browse_settings(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    text = (
        "⚙️ 浏览与解锁设置\n"
        f"浏览时限：{await get_config('browse_minutes','5')} 分钟（0=不限时）\n"
        f"到期限制：{await get_config('browse_scope','all')}\n"
        f"管理员免时限：{await get_config('browse_admin_exempt','1')}\n"
        f"解锁价格：{await get_config('unlock_price','10')} 积分\n"
        f"每日解锁上限：{await get_config('daily_unlock_limit','0')}（0=不限）\n"
        f"每日视频上限：{await get_config('daily_video_limit','0')}（0=不限）"
    )
    await callback.answer()
    await callback.message.edit_text(text, reply_markup=await settings_keyboard())


@dp.callback_query(F.data == "admin_menu_settings")
async def admin_menu_settings(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text("🧩 用户菜单设置\n可修改菜单名称、顺序、欢迎文案、帮助文案、公告和客服链接。", reply_markup=await menu_settings_keyboard())


@dp.callback_query(F.data.startswith("admin_toggle:"))
async def admin_toggle(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    key = callback.data.split(":", 1)[1]
    if key == "browse_scope":
        value = "videos" if await get_config(key, "all") == "all" else "all"
    elif key in ("browse_admin_exempt", "menu_show_random"):
        value = "0" if await get_config(key, "1") == "1" else "1"
    else:
        await callback.answer("不支持的设置", show_alert=True)
        return
    await set_config(key, value)
    await callback.answer("设置已保存")
    if key == "menu_show_random":
        await callback.message.edit_text("🧩 用户菜单设置已更新。", reply_markup=await menu_settings_keyboard())
    else:
        text = (
            "⚙️ 浏览与解锁设置\n"
            f"浏览时限：{await get_config('browse_minutes','5')} 分钟（0=不限时）\n"
            f"到期限制：{await get_config('browse_scope','all')}\n"
            f"管理员免时限：{await get_config('browse_admin_exempt','1')}\n"
            f"解锁价格：{await get_config('unlock_price','10')} 积分\n"
            f"每日解锁上限：{await get_config('daily_unlock_limit','0')}（0=不限）\n"
            f"每日视频上限：{await get_config('daily_video_limit','0')}（0=不限）"
        )
        await callback.message.edit_text(text, reply_markup=await settings_keyboard())


@dp.callback_query(F.data.startswith("admin_set:"))
async def admin_set_prompt(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    key = callback.data.split(":", 1)[1]
    if key not in set(DEFAULTS) - {"source_provider", "source_repo_id"}:
        await callback.answer("未知设置项", show_alert=True)
        return
    await callback.answer()
    prompt = {
        "browse_minutes": "请输入浏览分钟数（0 表示不限时，最大 1440）。",
        "unlock_price": "请输入解锁价格（积分）。",
        "daily_unlock_limit": "请输入每天最多解锁文件夹数（0 表示不限）。",
        "daily_video_limit": "请输入每日视频发送上限（0 表示不限）。",
        "browse_scope": "请发送 all 或 videos。",
        "browse_admin_exempt": "请发送 1（豁免）或 0（不豁免）。",
        "checkin_reward": "请输入每日签到奖励积分。",
        "initial_points": "请输入新用户初始积分。",
        "random_count": "请输入随机推荐数量设置（1–20）。",
        "buy_options": "请输入积分档位，用英文逗号分隔，例如 10,30,50。",
        "menu_order": "请输入菜单顺序，例如 my_info,checkin,random,buy,help。",
        "customer_service_url": "请输入客服链接；清空请发送 -。",
        "welcome_text": "请输入欢迎文案；清空请发送 -。",
        "help_text": "请输入帮助文案；清空请发送 -。",
        "announcement_text": "请输入公告文案；清空请发送 -。",
    }.get(key, "请输入新的设置值。")
    SEARCH_WAITING.add(callback.from_user.id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (f"pending_admin:{callback.from_user.id}", key))
        await db.commit()
    await callback.message.answer(f"{prompt}\n设置项：{key}\n保存后请重新打开菜单。")


@dp.callback_query(F.data == "admin_users_menu")
async def admin_users_menu(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        "👥 用户与会员管理\n"
        "调整积分：发送 /points 用户ID 积分变动（例如 /points 123456 20；扣减用负数）\n"
        "发放会员：发送 /member 用户ID 天数（0=永久，例如 /member 123456 30）\n"
        "封禁/解封：发送 /ban 用户ID。\n"
        "仅管理员可以执行。"
    )



@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*),COALESCE(SUM(points),0) FROM users") as cur:
            users, points = await cur.fetchone()
        async with db.execute("SELECT COUNT(*) FROM source_folders") as cur:
            folders = int((await cur.fetchone())[0] or 0)
        async with db.execute("SELECT COUNT(*) FROM source_resources") as cur:
            resources = int((await cur.fetchone())[0] or 0)
    await callback.answer()
    await callback.message.answer(f"📊 统计\n用户：{users}\n用户积分总额：{points}\n文件夹：{folders}\n资源记录：{resources}\n论坛导入：{await get_import_status_summary()}")


@dp.callback_query(F.data == "admin_code_prompt")
async def admin_code_prompt(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    SEARCH_WAITING.add(callback.from_user.id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (f"pending_admin:{callback.from_user.id}", "create_code"))
        await db.commit()
    await callback.answer()
    await callback.message.answer("请发送兑换码积分数，例如 30。机器人会生成一个兑换码。")


@dp.callback_query(F.data == "admin_backup")
async def admin_backup(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    await callback.answer("正在备份")
    try:
        import tempfile
        from aiogram.types import FSInputFile
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA wal_checkpoint(FULL)")
        await callback.message.answer_document(FSInputFile(DB_PATH), caption="悦己数据库备份。请妥善保管，包含用户和积分数据。")
    except Exception as exc:
        await callback.message.answer(f"备份失败：{str(exc)[:200]}")


@dp.message()
async def text_router(message: Message):
    if not message.from_user or not message.text:
        return
    uid = message.from_user.id
    await ensure_user(uid, message.from_user.username, message.from_user.first_name)
    if await user_is_banned(uid):
        await message.answer("账号暂时不可用。")
        return
    text = message.text.strip()
    if is_admin(uid) and text.startswith("/member "):
        try:
            _, target_text, days_text = text.split()
            target, days = int(target_text), int(days_text)
            if days < 0 or days > 36500:
                raise ValueError
        except ValueError:
            await message.answer("格式：/member 用户ID 天数（0=永久，最多 36500 天）")
            return
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT 1 FROM users WHERE user_id=?", (target,)) as cur:
                exists = await cur.fetchone()
            if not exists:
                await message.answer("用户不存在，请先让用户发送 /start。")
                return
            expiry = None if days == 0 else (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
            await db.execute("INSERT INTO memberships(user_id,expires_at,granted_by,updated_at) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET expires_at=excluded.expires_at,granted_by=excluded.granted_by,updated_at=excluded.updated_at", (target, expiry, uid, now_iso()))
            await db.commit()
        await message.answer(f"✅ 已为用户 {target} 发放{'永久会员' if days == 0 else str(days) + ' 天会员'}。")
        return
    if is_admin(uid) and text.startswith("/points "):
        try:
            _, target_text, amount_text = text.split()
            target, amount = int(target_text), int(amount_text)
        except ValueError:
            await message.answer("格式：/points 用户ID 积分变动，例如 /points 123456 20 或 /points 123456 -5")
            return
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT points FROM users WHERE user_id=?", (target,)) as cur:
                row = await cur.fetchone()
            if not row or int(row[0] or 0) + amount < 0:
                await message.answer("用户不存在，或扣减后积分将低于 0。")
                return
            await db.execute("UPDATE users SET points=points+? WHERE user_id=?", (amount, target))
            await db.execute("INSERT INTO points_log(user_id,amount,reason) VALUES(?,?,?)", (target, amount, f"管理员 {uid} 调整积分"))
            await db.commit()
        await message.answer(f"✅ 用户 {target} 积分已变动 {amount:+d}，当前余额 {int(row[0])+amount}。")
        return
    if is_admin(uid) and text.startswith("/ban "):
        try:
            target = int(text.split()[1])
        except (ValueError, IndexError):
            await message.answer("格式：/ban 用户ID")
            return
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT banned FROM users WHERE user_id=?", (target,)) as cur:
                row = await cur.fetchone()
            if not row:
                await message.answer("用户不存在。")
                return
            new_value = 0 if row[0] else 1
            await db.execute("UPDATE users SET banned=? WHERE user_id=?", (new_value, target))
            await db.commit()
        await message.answer(f"已{'封禁' if new_value else '解封'}用户 {target}。")
        return
    pending_key = f"pending_admin:{uid}"
    if is_admin(uid):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT value FROM config WHERE key=?", (pending_key,)) as cur:
                pending = await cur.fetchone()
        if pending:
            key = pending[0]
            raw = text
            if key == "create_code":
                try:
                    amount = int(raw)
                    if not 1 <= amount <= 100000:
                        raise ValueError
                except ValueError:
                    await message.answer("请输入 1–100000 的积分数。")
                    return
                import secrets
                code = secrets.token_urlsafe(8).replace("-", "").replace("_", "").upper()[:12]
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("INSERT INTO redemption_codes(code,points) VALUES(?,?)", (code, amount))
                    await db.execute("DELETE FROM config WHERE key=?", (pending_key,))
                    await db.commit()
                SEARCH_WAITING.discard(uid)
                await message.answer(f"兑换码已生成：\n<code>{code}</code>\n额度：{amount} 积分\n请仅发给已付款用户。")
                return
            if raw == "-":
                raw = ""
            value, error = await validate_config_value(key, raw)
            if error:
                await message.answer(f"设置未保存：{error}")
                return
            await set_config(key, value)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM config WHERE key=?", (pending_key,))
                await db.commit()
            SEARCH_WAITING.discard(uid)
            await message.answer(f"✅ 已保存 {key} = {value}", reply_markup=await reply_menu())
            return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM config WHERE key=?", (f"pending_redeem:{uid}",)) as cur:
            pending_redeem = await cur.fetchone()
    if text.lower().startswith("兑换码 ") or pending_redeem:
        code = (text.split(maxsplit=1)[1] if text.lower().startswith("兑换码 ") else text).strip().upper()
        if pending_redeem:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM config WHERE key=?", (f"pending_redeem:{uid}",))
                await db.commit()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute("SELECT points,used_by FROM redemption_codes WHERE code=?", (code,)) as cur:
                row = await cur.fetchone()
            if not row:
                await db.rollback()
                await message.answer("兑换码无效。")
                return
            if row[1] is not None:
                await db.rollback()
                await message.answer("这个兑换码已经使用过。")
                return
            amount = int(row[0])
            await db.execute("UPDATE redemption_codes SET used_by=?,used_at=? WHERE code=? AND used_by IS NULL", (uid, now_iso(), code))
            await db.execute("UPDATE users SET points=points+? WHERE user_id=?", (amount, uid))
            await db.execute("INSERT INTO points_log(user_id,amount,reason) VALUES(?,?,?)", (uid, amount, "兑换码"))
            await db.commit()
        await message.answer(f"🎉 兑换成功，增加 {amount} 积分。当前余额：{await user_points(uid)}")
        return
    labels = {await get_config(MENU_CONFIG[k], DEFAULTS[MENU_CONFIG[k]]): k for k in MENU_KEYS}
    action = labels.get(text)
    if action == "my_info":
        await show_user_info(message)
    elif action == "checkin":
        await handle_checkin(message)
    elif action == "random":
        await choose_random_source_folder(message, user_id=uid)
    elif action == "buy":
        await show_buy_options(message)
    elif action == "help":
        await message.answer(await get_config("help_text", DEFAULTS["help_text"]), reply_markup=await reply_menu())
    elif text == "📚 资源文件夹":
        await list_source_folders(message, 0)
    elif text == "🔍 搜索资源":
        SEARCH_WAITING.add(uid)
        await message.answer("请输入文件夹名称或关键词。")
    elif uid in SEARCH_WAITING:
        SEARCH_WAITING.discard(uid)
        query = text.strip()
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT source_folder_id,name,item_count,image_count,video_count,file_count FROM source_folders WHERE name LIKE ? AND item_count>0 ORDER BY name LIMIT 20", (f"%{query}%",)) as cur:
                rows = await cur.fetchall()
        if not rows:
            await message.answer("没有找到匹配的文件夹，请换一个关键词。")
        else:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"📁 {str(r[1])[:40]} · {r[2]}", callback_data=f"source_folder:{r[0]}")]
                for r in rows
            ])
            await message.answer(f"🔎 搜索「{query}」：找到 {len(rows)} 个文件夹。", reply_markup=kb)
    elif text == "取消":
        SEARCH_WAITING.discard(uid)
        await message.answer("已取消。", reply_markup=await reply_menu())
    else:
        await message.answer("请使用下方菜单，或发送 /folders 浏览资源、/search 搜索资源。", reply_markup=await reply_menu())


async def main():
    global BOT
    if not BOT_TOKEN:
        raise RuntimeError("请在 .env 中设置 BOT_TOKEN")
    await init_db()
    BOT = Bot(token=BOT_TOKEN)
    try:
        from bot_forum_ui import register_forum_ui
        register_forum_ui(dp, BOT, db_path=DB_PATH, is_admin=is_admin, get_config=get_config, set_config=set_config)
    except Exception as exc:
        log.exception("论坛管理界面注册失败：%s", exc)
    log.info("悦己机器人启动；数据库=%s", DB_PATH)
    await dp.start_polling(BOT, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
