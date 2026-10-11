import asyncio
import os
import random
import secrets
import json
from datetime import date
from datetime import datetime
from urllib.parse import urlparse, unquote

import aiomysql

import aiosqlite
from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    InputMediaPhoto,
    InputMediaVideo,
)
from dotenv import load_dotenv, dotenv_values

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not BOT_TOKEN:
    raise ValueError("请在 .env 文件中设置 BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DB_PATH = "yueji.db"

DEFAULT_CONFIG = {
    "start_points": "10",
    "checkin_min": "1",
    "checkin_max": "3",
    "buy_options": "10,30,50,100",
    "random_count": "3",
    "welcome_text": "🌷 欢迎来到「悦己」\n━━━━━━━━━━━━━━\n✨ 发现喜欢的内容，解锁专属资源\n🎁 每日签到领取积分\n🎲 随机探索精选资源\n👤 随时查看个人积分与解锁记录\n\n👇 请使用下方菜单开始体验",
    "help_text": "💡 悦己使用指南\n━━━━━━━━━━━━━━\n👤 我的信息：查看积分与解锁记录\n🎁 每日签到：每天领取随机积分奖励\n🎲 随机资源：浏览资源并使用积分解锁\n🛍️ 购买积分：查看积分档位并获取兑换码\n🎟️ 积分兑换：在购买积分页面输入兑换码\n\n🔐 解锁成功后，机器人会发送对应资源。\n如有疑问，请联系管理员。",
    "welcome_image_file_id": "",
    "source_repo_id": "",
    "source_folder_price": "10",
    "browse_minutes": "5",
    "browse_scope": "all",
    "browse_admin_exempt": "1",
    "daily_unlock_limit": "0",
    "menu_my_info_text": "👤 我的信息",
    "menu_checkin_text": "🎁 每日签到",
    "menu_random_text": "🎲 随机资源",
    "menu_buy_text": "🛍️ 购买积分",
    "menu_help_text": "💡 用户帮助",
    "menu_show_my_info": "1",
    "menu_show_checkin": "1",
    "menu_show_random": "1",
    "menu_show_buy": "1",
    "menu_show_help": "1",
    "menu_order": "my_info,checkin,random,buy,help",
    "announcement_text": "",
    "support_url": "",
}


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                points INTEGER DEFAULT 10,
                total_spent INTEGER DEFAULT 0,
                unlocked_count INTEGER DEFAULT 0,
                banned INTEGER NOT NULL DEFAULT 0
            )
        """)
        async with db.execute("PRAGMA table_info(users)") as cur:
            user_columns = {row[1] for row in await cur.fetchall()}
        if "banned" not in user_columns:
            await db.execute("ALTER TABLE users ADD COLUMN banned INTEGER NOT NULL DEFAULT 0")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                description TEXT,
                image_count INTEGER DEFAULT 0,
                video_count INTEGER DEFAULT 0,
                file_count INTEGER DEFAULT 0,
                price INTEGER DEFAULT 1,
                file_ids TEXT DEFAULT '[]'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS unlocks (
                user_id INTEGER,
                resource_id INTEGER,
                unlocked_at TEXT,
                PRIMARY KEY (user_id, resource_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS checkins (
                user_id INTEGER,
                checkin_date TEXT,
                PRIMARY KEY (user_id, checkin_date)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS redemption_codes (
                code TEXT PRIMARY KEY,
                points INTEGER NOT NULL,
                created_by INTEGER NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                used_by INTEGER,
                used_at TEXT
            )
        """)
        for k, v in DEFAULT_CONFIG.items():
            await db.execute(
                "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", (k, v)
            )
        await db.execute("""
            CREATE TABLE IF NOT EXISTS source_folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_folder_id TEXT UNIQUE,
                name TEXT NOT NULL,
                item_count INTEGER DEFAULT 0,
                image_count INTEGER DEFAULT 0,
                video_count INTEGER DEFAULT 0,
                file_count INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS source_resources (
                resource_key TEXT PRIMARY KEY,
                folder_id TEXT,
                chat_id TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                title TEXT,
                file_type TEXT,
                file_id TEXT,
                data TEXT
            )
        """)
        async with db.execute("PRAGMA table_info(source_resources)") as cur:
            existing_columns = {row[1] for row in await cur.fetchall()}
        if "file_id" not in existing_columns:
            await db.execute("ALTER TABLE source_resources ADD COLUMN file_id TEXT")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_source_resources_folder ON source_resources(folder_id)")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS source_folder_unlocks (
                user_id INTEGER NOT NULL,
                source_folder_id TEXT NOT NULL,
                unlocked_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, source_folder_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS source_browse_sessions (
                user_id INTEGER NOT NULL,
                source_folder_id TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at TEXT,
                PRIMARY KEY (user_id, source_folder_id)
            )
        """)
        await db.commit()


async def get_config(key: str, default=None):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM config WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else default


async def set_config(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value)
        )
        await db.commit()


async def validate_config_value(key: str, value: str):
    value = str(value).strip()
    numeric_ranges = {
        "start_points": (0, 1000000),
        "source_folder_price": (0, 1000000),
        "random_count": (1, 10),
        "browse_minutes": (0, 1440),
        "daily_unlock_limit": (0, 1000),
        "checkin_min": (0, 100000),
        "checkin_max": (0, 100000),
    }
    if key == "buy_options":
        options = [part.strip() for part in value.split(",")]
        if (not options or len(options) > 10
                or any(not part.isdigit() or not 1 <= int(part) <= 100000 for part in options)
                or len(set(options)) != len(options)):
            return None, "积分档位须为 1–100000 的正整数，最多 10 档且不能重复。"
        return ",".join(options), None
    if key in numeric_ranges:
        if not value.isdigit():
            return None, "该设置必须填写数字。"
        number = int(value)
        low, high = numeric_ranges[key]
        if not low <= number <= high:
            return None, f"数值范围应为 {low}–{high}。"
        if key == "checkin_min":
            high_value = int(await get_config("checkin_max", "3") or 0)
            if number > high_value:
                return None, "签到最低奖励不能高于当前最高奖励。"
        if key == "checkin_max":
            low_value = int(await get_config("checkin_min", "1") or 0)
            if number < low_value:
                return None, "签到最高奖励不能低于当前最低奖励。"
        return str(number), None
    menu_label_keys = {
        "menu_my_info_text", "menu_checkin_text", "menu_random_text",
        "menu_buy_text", "menu_help_text",
    }
    if key in menu_label_keys:
        if not value or len(value) > 32 or "\n" in value:
            return None, "菜单名称不能为空，最多 32 个字符且不能换行。"
        for other_key in menu_label_keys:
            if other_key != key and value == await get_config(other_key, DEFAULT_CONFIG[other_key]):
                return None, "菜单名称不能与其他菜单重复。"
        return value, None
    if key in ("menu_show_my_info", "menu_show_checkin", "menu_show_random", "menu_show_buy", "menu_show_help"):
        if value not in ("0", "1"):
            return None, "菜单显示状态无效。"
        return value, None
    if key == "menu_order":
        expected = {"my_info", "checkin", "random", "buy", "help"}
        order = [part.strip() for part in value.split(",") if part.strip()]
        if len(order) != len(expected) or set(order) != expected:
            return None, "菜单顺序格式无效。"
        return ",".join(order), None
    if key == "announcement_text":
        if len(value) > 1000:
            return None, "公告最多 1000 个字符。"
        return value, None
    if key == "support_url":
        if len(value) > 500:
            return None, "客服链接不能超过 500 个字符。"
        if value and (not value.startswith(("https://", "http://")) or not urlparse(value).netloc):
            return None, "客服链接请填写完整的 http:// 或 https:// URL。"
        return value, None
    if key == "browse_scope":
        if value not in ("all", "videos"):
            return None, "浏览范围只能设置为全部资源或视频。"
        return value, None
    if key == "browse_admin_exempt":
        if value not in ("0", "1"):
            return None, "管理员限时选项无效。"
        return value, None
    if key == "source_repo_id":
        if not value.lstrip("-").isdigit() or len(value) < 5:
            return None, "频道 ID 格式不正确，请发送数字频道 ID。"
        return value, None
    if key in ("welcome_text", "help_text"):
        if not value or len(value) > 3500:
            return None, "文案不能为空，且请控制在 3500 字以内。"
        return value, None
    if key == "welcome_image_file_id":
        return None, "请通过欢迎图片按钮发送图片，不支持手动输入此配置。"
    if key not in DEFAULT_CONFIG:
        return None, "无效配置键。"
    return value, None


def source_mysql_settings():
    # Read-only integration: first look at the existing bot's .env and PM2 saved
    # environment, then let Yueji's own environment override them. Never log secrets.
    cfg = {}
    try:
        cfg.update({k: v for k, v in dotenv_values("/root/bot/.env").items() if v})
    except Exception:
        pass
    try:
        with open("/root/.pm2/dump.pm2", "r", encoding="utf-8") as handle:
            saved_apps = json.load(handle)
        if isinstance(saved_apps, list):
            for app in saved_apps:
                name = str(app.get("name") or "")
                script = str((app.get("pm2_env") or {}).get("pm_exec_path") or "")
                if name == "bot" or "/root/bot/" in script:
                    env = (app.get("pm2_env") or {}).get("env") or {}
                    for key in ("DATABASE_URL", "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME"):
                        if env.get(key):
                            cfg[key] = env[key]
                    break
    except Exception:
        pass
    cfg.update({k: v for k, v in os.environ.items() if v})
    url = str(cfg.get("DATABASE_URL", "") or "").strip()
    if url:
        try:
            u = urlparse(url)
            return {
                "host": u.hostname,
                "port": int(u.port or 3306),
                "user": unquote(u.username or ""),
                "password": unquote(u.password or ""),
                "db": unquote(u.path.lstrip("/")),
                "charset": "utf8mb4",
                "connect_timeout": 8,
            }
        except Exception:
            return None
    host = str(cfg.get("DB_HOST", "") or "").strip()
    database = str(cfg.get("DB_NAME", "") or "").strip()
    if not host or not database:
        return None
    return {
        "host": host,
        "port": int(cfg.get("DB_PORT", 3306) or 3306),
        "user": str(cfg.get("DB_USER", "root") or "root"),
        "password": str(cfg.get("DB_PASSWORD", "") or ""),
        "db": database,
        "charset": "utf8mb4",
        "connect_timeout": 8,
    }


async def source_mysql_connect():
    cfg = source_mysql_settings()
    if not cfg:
        raise RuntimeError("未找到原资源机器人的 MySQL 配置。请确认 /root/bot/.env 含 DB_HOST、DB_NAME、DB_USER、DB_PASSWORD，或给悦己配置对应环境变量。")
    return await aiomysql.connect(**cfg)


async def detect_source_repository():
    # First use the configured repository ID, then try to discover the active repo
    # from the original bot's saved state in MySQL.
    saved = str(await get_config("source_repo_id", "") or "").strip()
    if saved:
        return saved
    conn = await source_mysql_connect()
    try:
        async with conn.cursor() as cur:
            await cur.execute("SELECT data FROM bot_state ORDER BY updated_at DESC LIMIT 30")
            rows = await cur.fetchall()
            for (raw,) in rows:
                try:
                    state = json.loads(raw or "{}")
                    repo = (state.get("settings") or {}).get("repository") or {}
                    chat_id = str(repo.get("chatId") or "").strip()
                    if chat_id:
                        return chat_id
                except Exception:
                    continue
            await cur.execute("SELECT chat_id FROM repositories WHERE status <> 'deleted' ORDER BY updated_at DESC LIMIT 1")
            row = await cur.fetchone()
            return str(row[0]).strip() if row and row[0] else ""
    finally:
        conn.close()


async def source_repository_status():
    try:
        conn = await source_mysql_connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute("SELECT COUNT(*) FROM folders WHERE status <> 'deleted'")
                folder_row = await cur.fetchone()
                await cur.execute("SELECT COUNT(*) FROM resources WHERE status <> 'deleted'")
                resource_row = await cur.fetchone()
                repo_id = await detect_source_repository()
                if repo_id:
                    await cur.execute("SELECT COUNT(*) FROM resources WHERE status <> 'deleted' AND chat_id=%s", (repo_id,))
                    selected_row = await cur.fetchone()
                else:
                    selected_row = (0,)
            return {
                "ok": True,
                "folders": int(folder_row[0] or 0),
                "resources_all": int(resource_row[0] or 0),
                "resources_selected": int(selected_row[0] or 0),
                "repo_id": repo_id,
            }
        finally:
            conn.close()
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:240]}


async def sync_source_repository():
    repo_id = await detect_source_repository()
    if not repo_id:
        raise RuntimeError("已连接 MySQL，但没能自动识别仓库频道 ID。请点击「手动绑定」后发送频道 ID（通常以 -100 开头）。")
    conn = await source_mysql_connect()
    try:
        async with conn.cursor() as cur:
            await cur.execute("SELECT id, name, data FROM folders WHERE status <> 'deleted' ORDER BY sort_order, name")
            folder_rows = await cur.fetchall()
            await cur.execute(
                "SELECT resource_key, folder_id, chat_id, message_id, title, file_type, file_id, data FROM resources WHERE status <> 'deleted' AND chat_id=%s ORDER BY message_id",
                (repo_id,),
            )
            resource_rows = await cur.fetchall()
    finally:
        conn.close()

    counts = {}
    for row in resource_rows:
        folder_id = str(row[1] or "")
        item_type = str(row[5] or "").lower()
        stat = counts.setdefault(folder_id, {"all": 0, "image": 0, "video": 0, "file": 0})
        stat["all"] += 1
        if "photo" in item_type or "image" in item_type:
            stat["image"] += 1
        elif "video" in item_type or "animation" in item_type:
            stat["video"] += 1
        else:
            stat["file"] += 1

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM source_resources")
        await db.execute("DELETE FROM source_folders")
        folder_map = {}
        for fid, name, raw in folder_rows:
            fid = str(fid)
            st = counts.get(fid, {"all": 0, "image": 0, "video": 0, "file": 0})
            await db.execute(
                "INSERT INTO source_folders (source_folder_id, name, item_count, image_count, video_count, file_count) VALUES (?, ?, ?, ?, ?, ?)",
                (fid, str(name or "未命名文件夹"), st["all"], st["image"], st["video"], st["file"]),
            )
            folder_map[fid] = True
        for key, folder_id, chat_id, message_id, title, file_type, file_id, raw in resource_rows:
            await db.execute(
                "INSERT OR REPLACE INTO source_resources (resource_key, folder_id, chat_id, message_id, title, file_type, file_id, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (str(key), str(folder_id or ""), str(chat_id), int(message_id or 0), str(title or ""), str(file_type or ""), str(file_id or ""), str(raw or "{}")),
            )
        await db.commit()
    await set_config("source_repo_id", repo_id)
    return {"repo_id": repo_id, "folders": len(folder_rows), "resources": len(resource_rows)}


async def source_repository_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 自动识别并绑定原仓库", callback_data="source_repo_auto")],
        [InlineKeyboardButton(text="✍️ 手动绑定频道 ID", callback_data="source_repo_manual")],
        [InlineKeyboardButton(text="🔄 同步文件夹与文件", callback_data="source_repo_sync")],
        [InlineKeyboardButton(text="📊 查看连接状态", callback_data="source_repo_status")],
        [InlineKeyboardButton(text="📁 浏览已同步文件夹", callback_data="source_repo_browse")],
        [InlineKeyboardButton(text="↩️ 返回设置中心", callback_data="settings_home")],
    ])


async def send_source_messages(chat_id: int, rows, preview: bool = False):
    # Preview sends exactly one source message. Unlock browsing sends at most 10
    # source messages per click; Telegram copyMessages preserves album grouping
    # when the source messages are grouped.
    grouped = {}
    for source_chat_id, message_id in rows:
        if not source_chat_id or not message_id:
            continue
        grouped.setdefault(str(source_chat_id), []).append(int(message_id))
    sent = 0
    for source_chat_id, message_ids in grouped.items():
        message_ids = sorted(set(message_ids))
        if preview:
            message_ids = message_ids[:1]
        for start in range(0, len(message_ids), 10 if not preview else 1):
            batch = message_ids[start:start + (10 if not preview else 1)]
            try:
                await bot.copy_messages(
                    chat_id=chat_id,
                    from_chat_id=source_chat_id,
                    message_ids=batch,
                    protect_content=True,
                )
                sent += len(batch)
            except Exception as exc:
                if preview:
                    raise RuntimeError("预览文件发送失败，请确认悦己机器人已加入原仓库且有读取权限：" + str(exc)[:250])
                print("Source resource batch copy error:", str(exc)[:200])
                continue
    return sent


async def send_source_folder_card(message: Message, folder, user_id: int | None = None):
    fid, name, total, images, videos, files = folder
    # On callback messages, message.from_user is the bot; use the actual clicker's ID.
    user_id = int(user_id or message.from_user.id)
    points, _, _ = await get_or_create_user(user_id, message.from_user.username or "")
    price_value = await get_config("source_folder_price", "10")
    price = max(0, int(price_value if price_value is not None else 10))
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT chat_id, message_id, file_type, file_id, data FROM source_resources WHERE folder_id=? ORDER BY message_id",
            (str(fid),),
        ) as cur:
            candidates = await cur.fetchall()
        async with db.execute(
            "SELECT 1 FROM source_folder_unlocks WHERE user_id=? AND source_folder_id=?",
            (user_id, str(fid)),
        ) as cur:
            already_unlocked = await cur.fetchone()

    text = (
        f"📁 {name}\n━━━━━━━━━━━━━━\n"
        f"🎬 视频/动画：{videos}\n🖼️ 图片：{images}\n📄 其他文件：{files}\n"
        f"📦 资源记录：{total}\n"
        f"🔓 整个文件夹解锁：{price} 积分\n💎 你的余额：{points} 积分\n\n"
        "预览仅供参考，解锁后可获取该文件夹的全部资源。"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🔓 解锁整个文件夹（{price} 积分）", callback_data=f"source_unlock:{fid}")],
        [InlineKeyboardButton(text="🎲 再随机一个文件夹", callback_data="source_random_again")],
    ])

    # 单条、非相册媒体优先直接发送：说明和解锁按钮附着在同一条媒体消息上。
    # 只使用同步数据库中的真实 file_id；没有可用 file_id 或属于相册时，
    # 回退到 copy_messages，保持原频道媒体组/相册结构，不拆散相册。
    def contains_media_group(value, depth=0):
        if depth > 6:
            return False
        if isinstance(value, dict):
            if value.get("media_group_id") or value.get("mediaGroupId"):
                return True
            return any(contains_media_group(v, depth + 1) for v in value.values())
        if isinstance(value, list):
            return any(contains_media_group(v, depth + 1) for v in value)
        return False

    for source_chat_id, message_id, file_type, stored_file_id, raw in candidates:
        kind = str(file_type or "").lower()
        file_id = str(stored_file_id or "").strip()
        if not file_id or not source_chat_id or not message_id:
            continue
        if not any(token in kind for token in ("photo", "image", "picture", "video", "animation", "document", "file")):
            continue
        try:
            data = json.loads(raw or "{}")
        except (TypeError, ValueError):
            data = {}
        if contains_media_group(data):
            continue

        try:
            send_kwargs = {
                "chat_id": message.chat.id,
                "caption": text,
                "reply_markup": kb,
                "protect_content": True,
            }
            if any(token in kind for token in ("photo", "image", "picture")):
                await bot.send_photo(photo=file_id, **send_kwargs)
            elif "animation" in kind:
                await bot.send_animation(animation=file_id, **send_kwargs)
            elif "video" in kind:
                await bot.send_video(video=file_id, **send_kwargs)
            else:
                await bot.send_document(document=file_id, **send_kwargs)
            return True
        except Exception as exc:
            # 例如 file_id 不属于当前机器人时，继续尝试其他单条媒体；
            # 最后仍会回退到按源消息 ID 复制，避免随机功能整体失效。
            print("Random folder single-media preview failed:", str(exc)[:220])

    # 回退路径：没有可直接发送的单条媒体时，保留原有说明卡片和源消息复制方式。
    if already_unlocked:
        await message.answer(text, reply_markup=kb)
        return True

    preview_rows = []
    seen = set()
    for source_chat_id, message_id, file_type, stored_file_id, raw in candidates:
        kind = str(file_type or "").lower()
        if not any(k in kind for k in ("photo", "image", "picture", "video")):
            continue
        key = (str(source_chat_id), int(message_id or 0))
        if not source_chat_id or not message_id or key in seen:
            continue
        seen.add(key)
        preview_rows.append((source_chat_id, int(message_id)))
        if len(preview_rows) >= 1:
            break

    await message.answer(text, reply_markup=kb)
    if preview_rows:
        try:
            await send_source_messages(message.chat.id, preview_rows, preview=True)
        except Exception as exc:
            print("Source preview copy error:", str(exc)[:300])
    return True


async def choose_random_source_folder(message: Message, user_id: int | None = None):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT source_folder_id, name, item_count, image_count, video_count, file_count "
            "FROM source_folders WHERE item_count > 0 ORDER BY RANDOM() LIMIT 1"
        ) as cur:
            folder = await cur.fetchone()
    if not folder:
        return False

    # Strict rule: one random click recommends exactly one folder card.
    # The random_count setting is retained for compatibility but cannot create
    # a burst of multiple folder recommendations.
    return await send_source_folder_card(message, folder, user_id=user_id)


@dp.callback_query(F.data == "source_random_again")
async def source_random_again(callback: CallbackQuery):
    await callback.answer()
    if not await choose_random_source_folder(callback.message, user_id=callback.from_user.id):
        await callback.message.answer("📭 暂时没有已同步的资源文件夹，请稍后再试。")


async def create_browse_session(user_id: int, fid: str):
    try:
        minutes = max(0, min(1440, int(await get_config("browse_minutes", "5") or 0)))
    except (TypeError, ValueError):
        minutes = 5
    admin_exempt = (await get_config("browse_admin_exempt", "1")) == "1" and is_admin(user_id)
    expires_at = None
    if minutes > 0 and not admin_exempt:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO source_browse_sessions (user_id, source_folder_id, started_at, expires_at)
                   VALUES (?, ?, datetime('now'), datetime('now', ?))
                   ON CONFLICT(user_id, source_folder_id) DO NOTHING""",
                (user_id, fid, f"+{minutes} minutes"),
            )
            await db.commit()
    else:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO source_browse_sessions (user_id, source_folder_id, started_at, expires_at)
                   VALUES (?, ?, datetime('now'), NULL)
                   ON CONFLICT(user_id, source_folder_id) DO NOTHING""",
                (user_id, fid),
            )
            await db.commit()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT started_at, expires_at FROM source_browse_sessions WHERE user_id=? AND source_folder_id=?",
            (user_id, fid),
        ) as cur:
            return await cur.fetchone()


async def browse_session_expired(user_id: int, fid: str):
    try:
        minutes = max(0, int(await get_config("browse_minutes", "5") or 0))
    except (TypeError, ValueError):
        minutes = 5
    if minutes == 0 or ((await get_config("browse_admin_exempt", "1")) == "1" and is_admin(user_id)):
        return False
    session = await create_browse_session(user_id, fid)
    if not session or not session[1]:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT CASE WHEN datetime('now') >= ? THEN 1 ELSE 0 END",
            (session[1],),
        ) as cur:
            row = await cur.fetchone()
    return bool(row and row[0])


def browse_start_keyboard(fid: str):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="▶️ 开始浏览资源", callback_data=f"source_browse_start:{fid}")
    ]])


async def send_browse_page_summary(message: Message, fid: str, rows, offset: int, sent: int, skipped_video_batch: bool = False):
    total = len(rows)
    next_offset = min(offset + 10, total)
    remaining = max(0, total - next_offset)
    if skipped_video_batch:
        note = "⏱️ 浏览时限已结束，本批包含视频，已跳过该批。其他不含视频的批次仍可继续。"
    else:
        note = f"📦 本批已发送 {sent} 个资源。"
    if remaining:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text=f"📦 继续发送（剩余 {remaining} 个）",
                callback_data=f"source_more:{fid}:{next_offset}",
            )
        ]])
        await message.answer(f"{note}\n剩余 {remaining} 个，点击下方继续。", reply_markup=kb)
    elif skipped_video_batch:
        await message.answer(note + "\n本文件夹已没有其他批次。")
    else:
        await message.answer(f"✅ 该文件夹资源已发送完毕，本批发送 {sent} 个。")


@dp.callback_query(F.data.startswith("source_unlock:"))
async def source_folder_unlock(callback: CallbackQuery):
    user_id = callback.from_user.id
    try:
        fid = str(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("文件夹参数无效", show_alert=True)
        return
    await get_or_create_user(user_id, callback.from_user.username or "")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT name, item_count FROM source_folders WHERE source_folder_id=?",
            (fid,),
        ) as cur:
            folder = await cur.fetchone()
        if not folder or int(folder[1] or 0) <= 0:
            await db.rollback()
            await callback.answer("这个文件夹没有可用资源", show_alert=True)
            return
        async with db.execute(
            "SELECT 1 FROM source_folder_unlocks WHERE user_id=? AND source_folder_id=?",
            (user_id, fid),
        ) as cur:
            existing = await cur.fetchone()
        if not existing:
            try:
                daily_limit = max(0, int(await get_config("daily_unlock_limit", "0") or 0))
            except (TypeError, ValueError):
                daily_limit = 0
            if daily_limit and not is_admin(user_id):
                async with db.execute(
                    "SELECT COUNT(*) FROM source_folder_unlocks WHERE user_id=? AND date(unlocked_at)=date('now')",
                    (user_id,),
                ) as cur:
                    daily_row = await cur.fetchone()
                used_today = int(daily_row[0] or 0) if daily_row else 0
                if used_today >= daily_limit:
                    await db.rollback()
                    await callback.answer(f"今天已达到文件夹解锁上限（{daily_limit} 个），请明天再来。", show_alert=True)
                    return
            async with db.execute("SELECT points FROM users WHERE user_id=?", (user_id,)) as cur:
                user_row = await cur.fetchone()
            price_value = await get_config("source_folder_price", "10")
            price = max(0, int(price_value if price_value is not None else 10))
            if not user_row or int(user_row[0]) < price:
                await db.rollback()
                await callback.answer(f"积分不足，需要 {price} 积分。", show_alert=True)
                return
            async with db.execute(
                "SELECT 1 FROM source_resources WHERE folder_id=? LIMIT 1",
                (fid,),
            ) as cur:
                has_resources = await cur.fetchone()
            if not has_resources:
                await db.rollback()
                await callback.answer("没有找到该文件夹的资源消息", show_alert=True)
                return
            await db.execute(
                "UPDATE users SET points=points-?, total_spent=total_spent+?, unlocked_count=unlocked_count+1 WHERE user_id=?",
                (price, price, user_id),
            )
            await db.execute(
                "INSERT INTO source_folder_unlocks (user_id, source_folder_id) VALUES (?, ?)",
                (user_id, fid),
            )
        await db.commit()
    await callback.answer("已解锁" if not existing else "你已解锁过，不会重复扣积分")
    unlocked_text = (
        f"🔓 文件夹「{folder[0]}」已解锁。\n\n"
        "点击「开始浏览资源」后开始计时；时间到后，机器人将停止发送受限资源。已发送的内容无法撤回。"
    )
    keyboard = browse_start_keyboard(fid)
    try:
        # 普通说明卡片用 edit_text；媒体预览卡片必须用 edit_caption，
        # 否则积分扣除成功后 Telegram 会因“消息没有文本”而报错。
        if callback.message.caption is not None:
            await callback.message.edit_caption(caption=unlocked_text, reply_markup=keyboard)
        else:
            await callback.message.edit_text(unlocked_text, reply_markup=keyboard)
    except Exception as exc:
        print("Source unlock confirmation edit failed:", str(exc)[:220])
        await callback.message.answer(unlocked_text, reply_markup=keyboard)


@dp.callback_query(F.data.startswith("source_browse_start:"))
async def source_browse_start(callback: CallbackQuery):
    try:
        fid = str(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("文件夹参数无效", show_alert=True)
        return
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM source_folder_unlocks WHERE user_id=? AND source_folder_id=?",
            (user_id, fid),
        ) as cur:
            unlocked = await cur.fetchone()
        async with db.execute(
            "SELECT chat_id, message_id, file_type FROM source_resources WHERE folder_id=? ORDER BY message_id",
            (fid,),
        ) as cur:
            rows = await cur.fetchall()
    if not unlocked:
        await callback.answer("请先解锁这个文件夹。", show_alert=True)
        return
    if not rows:
        await callback.answer("该文件夹没有可发送的资源。", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM source_browse_sessions WHERE user_id=? AND source_folder_id=?",
            (user_id, fid),
        ) as cur:
            existing_session = await cur.fetchone()
    if existing_session:
        if await browse_session_expired(user_id, fid):
            await callback.answer("该文件夹的浏览时限已结束，不能重新开始计时。", show_alert=True)
        else:
            await callback.answer("浏览计时已经开始，请使用原有的「继续发送」按钮，避免重复发送。", show_alert=True)
        return
    session = await create_browse_session(user_id, fid)
    if await browse_session_expired(user_id, fid):
        await callback.answer("该文件夹的浏览时限已结束，不能重新开始计时。", show_alert=True)
        return
    try:
        minutes = max(0, int(await get_config("browse_minutes", "5") or 0))
    except (TypeError, ValueError):
        minutes = 5
    admin_exempt = (await get_config("browse_admin_exempt", "1")) == "1" and is_admin(user_id)
    if minutes == 0:
        time_text = "本文件夹未设置浏览时限。"
    elif admin_exempt:
        time_text = "管理员账号不受浏览时限限制。"
    elif session and session[1]:
        time_text = f"本次浏览时限：{minutes} 分钟，从点击开始时计算。"
    else:
        time_text = "浏览计时已启动。"
    await callback.answer("开始浏览")
    try:
        await callback.message.edit_text(f"▶️ 已开始浏览文件夹。\n{time_text}\n时间到后，已发送内容不会撤回。")
    except Exception:
        await callback.message.answer(f"▶️ 已开始浏览文件夹。\n{time_text}")
    first_page = rows[:10]
    sent = await send_source_messages(callback.message.chat.id, [(r[0], r[1]) for r in first_page])
    await send_browse_page_summary(callback.message, fid, rows, 0, sent)


@dp.callback_query(F.data.startswith("source_more:"))
async def source_folder_more(callback: CallbackQuery):
    try:
        _, fid, offset_text = callback.data.split(":", 2)
        offset = max(0, int(offset_text))
    except (ValueError, TypeError):
        await callback.answer("分页参数无效，请重新进入文件夹。", show_alert=True)
        return

    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM source_folder_unlocks WHERE user_id=? AND source_folder_id=?",
            (user_id, fid),
        ) as cur:
            unlocked = await cur.fetchone()
        async with db.execute(
            "SELECT chat_id, message_id, file_type FROM source_resources WHERE folder_id=? ORDER BY message_id",
            (fid,),
        ) as cur:
            rows = await cur.fetchall()
    if not unlocked:
        await callback.answer("请先解锁这个文件夹。", show_alert=True)
        return
    if offset >= len(rows):
        await callback.answer("这个文件夹的资源已经发送完了。", show_alert=True)
        return

    expired = await browse_session_expired(user_id, fid)
    scope = await get_config("browse_scope", "all")
    page = rows[offset:offset + 10]
    has_video = any(any(kind in str(row[2] or "").lower() for kind in ("video", "animation")) for row in page)
    if expired and scope != "videos":
        await callback.answer("浏览时限已结束，无法继续发送该文件夹资源。", show_alert=True)
        return
    await callback.answer("正在发送下一批")
    if expired and scope == "videos" and has_video:
        await send_browse_page_summary(callback.message, fid, rows, offset, 0, skipped_video_batch=True)
        return
    sent = await send_source_messages(callback.message.chat.id, [(r[0], r[1]) for r in page])
    await send_browse_page_summary(callback.message, fid, rows, offset, sent)


async def get_or_create_user(user_id: int, username: str):
    start_points = int(await get_config("start_points", "10"))
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT points, total_spent, unlocked_count FROM users WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            await db.execute(
                "INSERT INTO users (user_id, username, points) VALUES (?, ?, ?)",
                (user_id, username, start_points),
            )
            await db.commit()
            return start_points, 0, 0
        return row


def is_admin(user_id: int) -> bool:
    return bool(ADMIN_ID and user_id == ADMIN_ID)


class UserAccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if not user or is_admin(user.id):
            return await handler(event, data)
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT banned FROM users WHERE user_id=?", (user.id,)) as cur:
                row = await cur.fetchone()
        if row and int(row[0] or 0):
            if isinstance(event, CallbackQuery):
                await event.answer("你的账号当前无法使用该机器人，请联系管理员。", show_alert=True)
            elif isinstance(event, Message):
                await event.answer("你的账号当前无法使用该机器人，请联系管理员。")
            return None
        return await handler(event, data)


dp.message.outer_middleware(UserAccessMiddleware())
dp.callback_query.outer_middleware(UserAccessMiddleware())


async def main_keyboard(user_id: int = 0):
    definitions = {
        "my_info": ("menu_show_my_info", "menu_my_info_text"),
        "checkin": ("menu_show_checkin", "menu_checkin_text"),
        "random": ("menu_show_random", "menu_random_text"),
        "buy": ("menu_show_buy", "menu_buy_text"),
        "help": ("menu_show_help", "menu_help_text"),
    }
    raw_order = await get_config("menu_order", DEFAULT_CONFIG["menu_order"])
    order = [part.strip() for part in str(raw_order).split(",") if part.strip() in definitions]
    order.extend(key for key in definitions if key not in order)
    menu_items = [definitions[key] for key in order]
    visible = []
    for show_key, text_key in menu_items:
        if await get_config(show_key, "1") == "1":
            visible.append(KeyboardButton(text=await get_config(text_key, DEFAULT_CONFIG[text_key])))
    rows = []
    if visible:
        rows.append(visible[:3])
    if len(visible) > 3:
        rows.append(visible[3:])
    if is_admin(user_id):
        rows.append([KeyboardButton(text="⚙️ 设置中心")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await get_or_create_user(message.from_user.id, message.from_user.username or "")
    welcome = await get_config("welcome_text") or DEFAULT_CONFIG["welcome_text"]
    image_id = await get_config("welcome_image_file_id", "")
    announcement = await get_config("announcement_text", "")
    if announcement:
        welcome += "\n\n📢 公告\n━━━━━━━━━━━━━━\n" + announcement
    support_url = await get_config("support_url", "")
    if support_url:
        welcome += "\n\n🆘 客服：" + support_url
    welcome = welcome[:3900]
    keyboard = await main_keyboard(message.from_user.id)
    if image_id:
        try:
            if len(welcome) <= 1024:
                await message.answer_photo(image_id, caption=welcome, reply_markup=keyboard)
            else:
                await message.answer_photo(image_id, caption="🌷 欢迎来到「悦己」")
                await message.answer(welcome, reply_markup=keyboard)
        except Exception:
            await message.answer(welcome, reply_markup=keyboard)
    else:
        await message.answer(welcome, reply_markup=keyboard)


@dp.message(Command("config"))
async def cmd_config(message: Message):
    if not is_admin(message.from_user.id):
        return
    text = "当前配置：\n"
    for k in DEFAULT_CONFIG:
        v = await get_config(k)
        text += f"• {k} = {v}\n"
    text += "\n修改方法：/set 键 值\n例如：/set start_points 20\n/set buy_options 5,15,30,60,100\n/set help_text 新的帮助文案"
    await message.answer(text)


@dp.message(Command("set"))
async def cmd_set(message: Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("用法：/set 键 值")
        return
    key, value = args[1], args[2]
    normalized, error = await validate_config_value(key, value)
    if error:
        await message.answer(error)
        return
    await set_config(key, normalized)
    await message.answer(f"✅ 已更新 {key} = {normalized}")


@dp.message(F.text == "⚙️ 设置中心")
async def settings_center(message: Message):
    if not is_admin(message.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 新用户赠送积分", callback_data="settings_start_points")],
        [InlineKeyboardButton(text="📅 每日签到奖励", callback_data="settings_checkin")],
        [InlineKeyboardButton(text="🛍️ 积分购买档位", callback_data="settings_buy_options")],
        [InlineKeyboardButton(text="🎲 随机文件夹数量", callback_data="settings_random_count")],
        [InlineKeyboardButton(text="📦 文件夹解锁价格", callback_data="settings_source_price")],
        [InlineKeyboardButton(text="⏱️ 解锁后浏览时长", callback_data="settings_browse_limit")],
        [InlineKeyboardButton(text="📅 每日解锁上限", callback_data="settings_daily_unlock_limit")],
        [InlineKeyboardButton(text="🧩 用户菜单管理", callback_data="settings_menu_manage")],
        [InlineKeyboardButton(text="📢 公告文案", callback_data="settings_text:announcement_text"), InlineKeyboardButton(text="🆘 客服链接", callback_data="settings_text:support_url")],
        [InlineKeyboardButton(text="✏️ 修改欢迎文案", callback_data="settings_text:welcome_text"), InlineKeyboardButton(text="💡 修改帮助文案", callback_data="settings_text:help_text")],
        [InlineKeyboardButton(text="🖼️ 设置欢迎图片", callback_data="settings_welcome_image")],
        [InlineKeyboardButton(text="🎟️ 兑换码管理", callback_data="codes_manage")],
        [InlineKeyboardButton(text="📚 资源仓库管理", callback_data="source_repo_manage")],
        [InlineKeyboardButton(text="👥 用户与积分管理", callback_data="admin_users")],
        [InlineKeyboardButton(text="💾 发送数据库备份", callback_data="admin_db_backup")],
        [InlineKeyboardButton(text="📋 查看当前设置", callback_data="settings_show")],
    ])
    await message.answer("⚙️ 悦己设置中心\n━━━━━━━━━━━━━━\n请选择要调整的项目。大部分设置可直接点击完成。", reply_markup=kb)


@dp.callback_query(F.data == "settings_home")
async def settings_home_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 新用户赠送积分", callback_data="settings_start_points")],
        [InlineKeyboardButton(text="📅 每日签到奖励", callback_data="settings_checkin")],
        [InlineKeyboardButton(text="🛍️ 积分购买档位", callback_data="settings_buy_options")],
        [InlineKeyboardButton(text="🎲 随机文件夹数量", callback_data="settings_random_count")],
        [InlineKeyboardButton(text="📦 文件夹解锁价格", callback_data="settings_source_price")],
        [InlineKeyboardButton(text="⏱️ 解锁后浏览时长", callback_data="settings_browse_limit")],
        [InlineKeyboardButton(text="📅 每日解锁上限", callback_data="settings_daily_unlock_limit")],
        [InlineKeyboardButton(text="🧩 用户菜单管理", callback_data="settings_menu_manage")],
        [InlineKeyboardButton(text="📢 公告文案", callback_data="settings_text:announcement_text"), InlineKeyboardButton(text="🆘 客服链接", callback_data="settings_text:support_url")],
        [InlineKeyboardButton(text="✏️ 修改欢迎文案", callback_data="settings_text:welcome_text"), InlineKeyboardButton(text="💡 修改帮助文案", callback_data="settings_text:help_text")],
        [InlineKeyboardButton(text="🖼️ 设置欢迎图片", callback_data="settings_welcome_image")],
        [InlineKeyboardButton(text="🎟️ 兑换码管理", callback_data="codes_manage")],
        [InlineKeyboardButton(text="📚 资源仓库管理", callback_data="source_repo_manage")],
        [InlineKeyboardButton(text="👥 用户与积分管理", callback_data="admin_users")],
        [InlineKeyboardButton(text="💾 发送数据库备份", callback_data="admin_db_backup")],
        [InlineKeyboardButton(text="📋 查看当前设置", callback_data="settings_show")],
    ])
    await callback.message.edit_text("⚙️ 悦己设置中心\n━━━━━━━━━━━━━━\n请选择要调整的项目。", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data == "source_repo_manage")
async def source_repo_manage(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    repo_id = str(await get_config("source_repo_id", "") or "").strip()
    state = await source_repository_status()
    if state["ok"]:
        status_text = (
            "🟢 MySQL 可连接\n"
            f"📁 原库文件夹：{state['folders']} 个\n"
            f"📚 原库资源记录：{state['resources_all']} 条\n"
            f"🔗 当前绑定频道：{repo_id or state['repo_id'] or '尚未绑定'}\n"
            f"📦 该频道资源：{state['resources_selected']} 条"
        )
    else:
        status_text = "🔴 MySQL 暂不可连接\n" + state["error"]
    await callback.message.edit_text(
        "📚 资源仓库管理\n━━━━━━━━━━━━━━\n"
        + status_text
        + "\n\n此功能只读取原资源库的文件夹和消息索引，不会修改或重启原机器人。"
        + "\n请先绑定，再同步文件夹与文件。",
        reply_markup=await source_repository_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data == "source_repo_auto")
async def source_repo_auto(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    try:
        repo_id = await detect_source_repository()
        if not repo_id:
            await callback.message.answer("没有自动识别到仓库频道，请点击「手动绑定频道 ID」。")
        else:
            await set_config("source_repo_id", repo_id)
            await callback.message.answer(f"✅ 已识别并绑定原仓库频道：{repo_id}\n接下来点击「同步文件夹与文件」。")
    except Exception as exc:
        await callback.message.answer("❌ 连接原资源库失败：\n" + str(exc)[:700])
    await callback.answer()


@dp.callback_query(F.data == "source_repo_manual")
async def source_repo_manual(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    pending_config[callback.from_user.id] = "source_repo_id"
    await callback.message.answer("✍️ 请发送原资源仓库的频道 ID（例如 -100 开头的数字）。\n发送「取消设置」可取消。")
    await callback.answer()


@dp.callback_query(F.data == "source_repo_sync")
async def source_repo_sync(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    await callback.answer("正在同步，请稍候…")
    try:
        result = await sync_source_repository()
        await callback.message.answer(
            "✅ 原资源索引同步完成\n━━━━━━━━━━━━━━\n"
            f"📁 文件夹：{result['folders']} 个\n"
            f"📚 文件/消息记录：{result['resources']} 条\n"
            f"🔗 绑定频道：{result['repo_id']}\n\n"
            "本次仅同步索引，不移动或删除原仓库文件。"
        )
    except Exception as exc:
        await callback.message.answer("❌ 同步失败：\n" + str(exc)[:900])
        

@dp.callback_query(F.data == "source_repo_status")
async def source_repo_status(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    state = await source_repository_status()
    if state["ok"]:
        body = (
            "🟢 MySQL 连接正常\n"
            f"📁 文件夹总数：{state['folders']}\n"
            f"📚 原库资源总数：{state['resources_all']}\n"
            f"🔗 绑定频道：{await get_config('source_repo_id', '') or state['repo_id'] or '未绑定'}\n"
            f"📦 频道资源记录：{state['resources_selected']}"
        )
    else:
        body = "🔴 连接失败\n" + state["error"]
    await callback.message.answer(body)
    await callback.answer()


@dp.callback_query(F.data == "source_repo_browse")
async def source_repo_browse(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, name, item_count, image_count, video_count, file_count FROM source_folders ORDER BY name LIMIT 30"
        ) as cur:
            rows = await cur.fetchall()
    if not rows:
        await callback.message.answer("目前没有已同步的文件夹。请先点击「同步文件夹与文件」。")
    else:
        buttons = []
        for fid, name, total, images, videos, files in rows:
            label = str(name)[:36]
            buttons.append([InlineKeyboardButton(
                text=f"📁 {label}（{total}）",
                callback_data=f"source_folder:{fid}"
            )])
        buttons.append([InlineKeyboardButton(text="↩️ 返回资源仓库管理", callback_data="source_repo_manage")])
        await callback.message.answer("📁 已同步文件夹（最多显示 30 个）\n点击文件夹查看记录：", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@dp.callback_query(F.data.startswith("source_folder:"))
async def source_folder_detail(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    try:
        fid = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer("文件夹编号无效", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT source_folder_id, name, item_count, image_count, video_count, file_count FROM source_folders WHERE id=?",
            (fid,),
        ) as cur:
            folder = await cur.fetchone()
        if folder:
            async with db.execute(
                "SELECT title, chat_id, message_id, file_type FROM source_resources WHERE folder_id=? ORDER BY message_id LIMIT 10",
                (folder[0],),
            ) as cur:
                items = await cur.fetchall()
    if not folder:
        await callback.answer("文件夹不存在，请重新同步", show_alert=True)
        return
    source_id, name, total, images, videos, files = folder
    text = (
        f"📁 {name}\n━━━━━━━━━━━━━━\n"
        f"🎬 视频/动画：{videos}\n🖼️ 图片：{images}\n📄 其他文件：{files}\n"
        f"📦 资源记录：{total}\n\n"
        "前 10 条资源索引：\n"
    )
    if items:
        text += "\n".join(f"• {str(title or '未命名')[:90]}（消息 {mid}）" for title, chat, mid, typ in items)
    else:
        text += "这个文件夹目前没有匹配到该仓库频道的资源记录。"
    await callback.message.answer(text[:4000], reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📁 返回文件夹列表", callback_data="source_repo_browse")],
        [InlineKeyboardButton(text="📚 返回资源仓库管理", callback_data="source_repo_manage")],
    ]))
    await callback.answer()


@dp.callback_query(F.data == "codes_manage")
async def codes_manage(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="生成 10 积分码", callback_data="codes_points:10"),
         InlineKeyboardButton(text="生成 30 积分码", callback_data="codes_points:30")],
        [InlineKeyboardButton(text="生成 50 积分码", callback_data="codes_points:50"),
         InlineKeyboardButton(text="生成 100 积分码", callback_data="codes_points:100")],
        [InlineKeyboardButton(text="↩️ 返回设置", callback_data="settings_home")],
    ])
    await callback.message.edit_text(
        "🎟️ 兑换码管理\n━━━━━━━━━━━━━━\n先选择每个兑换码对应的积分，再选择生成数量。\n每个兑换码只能成功兑换一次。",
        reply_markup=kb,
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("codes_points:"))
async def codes_choose_quantity(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    points = callback.data.split(":", 1)[1]
    if not points.isdigit() or int(points) not in (10, 30, 50, 100):
        await callback.answer("积分档位无效", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="生成 1 个", callback_data=f"codes_generate:{points}:1"),
         InlineKeyboardButton(text="生成 5 个", callback_data=f"codes_generate:{points}:5")],
        [InlineKeyboardButton(text="生成 10 个", callback_data=f"codes_generate:{points}:10"),
         InlineKeyboardButton(text="生成 20 个", callback_data=f"codes_generate:{points}:20")],
        [InlineKeyboardButton(text="↩️ 返回兑换码管理", callback_data="codes_manage")],
    ])
    await callback.message.edit_text(f"🎟️ 每个兑换码价值 {points} 积分。\n请选择本次生成数量：", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data.startswith("codes_generate:"))
async def codes_generate(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    try:
        _, points_s, quantity_s = callback.data.split(":")
        points, quantity = int(points_s), int(quantity_s)
    except (ValueError, TypeError):
        await callback.answer("参数无效", show_alert=True)
        return
    if points not in (10, 30, 50, 100) or quantity not in (1, 5, 10, 20):
        await callback.answer("生成参数无效", show_alert=True)
        return
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    generated = []
    async with aiosqlite.connect(DB_PATH) as db:
        for _ in range(quantity):
            code = "YJ-" + "".join(secrets.choice(alphabet) for _ in range(10))
            await db.execute(
                "INSERT INTO redemption_codes (code, points, created_by) VALUES (?, ?, ?)",
                (code, points, callback.from_user.id),
            )
            generated.append(code)
        await db.commit()
    body = "\n".join(generated)
    await callback.message.answer(
        f"✅ 已生成 {quantity} 个兑换码\n每个兑换码：{points} 积分\n\n{body}\n\n请妥善保管并只发给已完成付款的用户。兑换码可使用一次。"
    )
    await callback.answer("生成成功")
    await callback.message.edit_reply_markup(reply_markup=None)


@dp.callback_query(F.data == "admin_user_search")
async def admin_user_search(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    pending_config[callback.from_user.id] = "admin_user_search"
    await callback.message.answer("🔍 请发送用户的 Telegram 数字 ID 或用户名（可带 @）。发送「取消设置」可退出。")
    await callback.answer()


@dp.callback_query(F.data == "admin_users_clear_search")
async def admin_users_clear_search(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    admin_user_search_queries.pop(callback.from_user.id, None)
    await admin_users(callback, answer_callback=False)
    await callback.answer()


@dp.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery, answer_callback: bool = True):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    query = admin_user_search_queries.get(callback.from_user.id, "").strip().lstrip("@")
    async with aiosqlite.connect(DB_PATH) as db:
        if query:
            async with db.execute(
                """SELECT user_id, username, points, banned FROM users
                   WHERE CAST(user_id AS TEXT)=? OR username LIKE ?
                   ORDER BY rowid DESC LIMIT 10""",
                (query, f"%{query}%"),
            ) as cur:
                rows = await cur.fetchall()
            async with db.execute(
                """SELECT COUNT(*) FROM users
                   WHERE CAST(user_id AS TEXT)=? OR username LIKE ?""",
                (query, f"%{query}%"),
            ) as cur:
                count_row = await cur.fetchone()
        else:
            async with db.execute(
                "SELECT user_id, username, points, banned FROM users ORDER BY rowid DESC LIMIT 10"
            ) as cur:
                rows = await cur.fetchall()
            async with db.execute("SELECT COUNT(*) FROM users") as cur:
                count_row = await cur.fetchone()
    title = "🔍 用户搜索结果" if query else "👥 用户与积分管理"
    lines = [
        title,
        "━━━━━━━━━━━━━━",
        f"👤 用户总数：{int(count_row[0] or 0)}" + (f"（搜索：{query}）" if query else ""),
        "显示最多 10 位用户，可增减积分或封禁/解封。",
        "",
    ]
    buttons = []
    for uid, username, points, banned in rows:
        label = (("@" + username) if username else str(uid))[:24]
        lines.append(f"• {label}（ID {uid}）：{int(points or 0)} 积分｜{'🚫 已封禁' if banned else '🟢 正常'}")
        buttons.append([
            InlineKeyboardButton(text="➕10", callback_data=f"admin_points:{uid}:10"),
            InlineKeyboardButton(text="➕50", callback_data=f"admin_points:{uid}:50"),
            InlineKeyboardButton(text="➖10", callback_data=f"admin_points:{uid}:-10"),
            InlineKeyboardButton(text=("🔓 解封" if banned else "🚫 封禁"), callback_data=f"admin_ban:{uid}:{0 if banned else 1}"),
        ])
    if not rows:
        lines.append("没有找到匹配用户。")
    buttons.append([InlineKeyboardButton(text="🔍 搜索用户", callback_data="admin_user_search")])
    if query:
        buttons.append([InlineKeyboardButton(text="📋 返回最近用户", callback_data="admin_users_clear_search")])
    buttons.append([InlineKeyboardButton(text="🔄 刷新", callback_data="admin_users")])
    buttons.append([InlineKeyboardButton(text="↩️ 返回设置中心", callback_data="settings_home")])
    text = "\n".join(lines)
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard)
    if answer_callback:
        await callback.answer()


@dp.callback_query(F.data.startswith("admin_ban:"))
async def admin_ban_user(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    try:
        _, uid_text, banned_text = callback.data.split(":")
        target_id, banned = int(uid_text), int(banned_text)
    except (ValueError, TypeError):
        await callback.answer("用户参数无效", show_alert=True)
        return
    if banned not in (0, 1):
        await callback.answer("封禁参数无效", show_alert=True)
        return
    if target_id == ADMIN_ID:
        await callback.answer("不能封禁机器人管理员。", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM users WHERE user_id=?", (target_id,)) as cur:
            exists = await cur.fetchone()
        if not exists:
            await callback.answer("用户不存在", show_alert=True)
            return
        await db.execute("UPDATE users SET banned=? WHERE user_id=?", (banned, target_id))
        await db.commit()
    await callback.answer("已封禁用户" if banned else "已解除封禁")
    await admin_users(callback, answer_callback=False)


@dp.callback_query(F.data.startswith("admin_points:"))
async def admin_adjust_points(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    try:
        _, uid_text, delta_text = callback.data.split(":")
        target_id, delta = int(uid_text), int(delta_text)
    except (ValueError, TypeError):
        await callback.answer("参数无效", show_alert=True)
        return
    if delta not in (-50, -10, 10, 50):
        await callback.answer("不支持的积分调整值", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT points FROM users WHERE user_id=?", (target_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            await callback.answer("用户不存在", show_alert=True)
            return
        new_points = max(0, int(row[0] or 0) + delta)
        actual_delta = new_points - int(row[0] or 0)
        await db.execute("UPDATE users SET points=? WHERE user_id=?", (new_points, target_id))
        await db.commit()
    await callback.answer(f"积分已调整：{actual_delta:+d}，余额 {new_points}")
    await admin_users(callback, answer_callback=False)


@dp.callback_query(F.data == "settings_start_points")
async def settings_start_points(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True); return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"{n} 积分", callback_data=f"cfgset:start_points:{n}") for n in (0, 10, 20, 50, 100)
    ], [InlineKeyboardButton(text="↩️ 返回设置", callback_data="settings_home")]])
    await callback.message.edit_text("🎁 请选择新用户注册时获得的初始积分：", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data == "settings_checkin")
async def settings_checkin(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True); return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="固定 1 分", callback_data="cfgcheck:1:1"), InlineKeyboardButton(text="随机 1–3 分", callback_data="cfgcheck:1:3")],
        [InlineKeyboardButton(text="随机 1–5 分", callback_data="cfgcheck:1:5"), InlineKeyboardButton(text="随机 3–10 分", callback_data="cfgcheck:3:10")],
        [InlineKeyboardButton(text="↩️ 返回设置", callback_data="settings_home")],
    ])
    await callback.message.edit_text("📅 请选择每日签到奖励范围：", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data == "settings_buy_options")
async def settings_buy_options(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True); return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="10 / 30 / 50 / 100", callback_data="cfgset:buy_options:10,30,50,100")],
        [InlineKeyboardButton(text="5 / 15 / 30 / 60 / 100", callback_data="cfgset:buy_options:5,15,30,60,100")],
        [InlineKeyboardButton(text="20 / 50 / 100 / 200", callback_data="cfgset:buy_options:20,50,100,200")],
        [InlineKeyboardButton(text="✏️ 自定义积分档位", callback_data="settings_buy_options_custom")],
        [InlineKeyboardButton(text="↩️ 返回设置", callback_data="settings_home")],
    ])
    await callback.message.edit_text("🛍️ 请选择积分购买档位。提示：当前购买按钮仅为档位展示，真实支付尚未接入。", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data == "settings_buy_options_custom")
async def settings_buy_options_custom(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    pending_config[callback.from_user.id] = "buy_options"
    await callback.message.answer("请发送积分档位列表，例如：10,30,50,100。最多 10 档，数值 1–100000 且不能重复。发送「取消设置」可退出。")
    await callback.answer()


@dp.callback_query(F.data == "settings_source_price")
async def settings_source_price(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{n} 积分", callback_data=f"cfgset:source_folder_price:{n}") for n in (1, 5, 10)],
        [InlineKeyboardButton(text=f"{n} 积分", callback_data=f"cfgset:source_folder_price:{n}") for n in (20, 50, 100)],
        [InlineKeyboardButton(text="↩️ 返回设置", callback_data="settings_home")],
    ])
    current = await get_config("source_folder_price", "10")
    await callback.message.edit_text(f"📦 当前每个资源文件夹解锁价格：{current} 积分\n请选择新价格：", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data == "settings_daily_unlock_limit")
async def settings_daily_unlock_limit(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    current = await get_config("daily_unlock_limit", "0")
    label = "不限量" if current == "0" else f"每天最多 {current} 个文件夹"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="不限量", callback_data="cfgset:daily_unlock_limit:0"),
            InlineKeyboardButton(text="1 个", callback_data="cfgset:daily_unlock_limit:1"),
            InlineKeyboardButton(text="3 个", callback_data="cfgset:daily_unlock_limit:3"),
        ],
        [
            InlineKeyboardButton(text="5 个", callback_data="cfgset:daily_unlock_limit:5"),
            InlineKeyboardButton(text="10 个", callback_data="cfgset:daily_unlock_limit:10"),
            InlineKeyboardButton(text="自定义", callback_data="settings_daily_unlock_custom"),
        ],
        [InlineKeyboardButton(text="↩️ 返回设置中心", callback_data="settings_home")],
    ])
    await callback.message.edit_text(
        f"📅 每日文件夹解锁上限\n当前：{label}\n\n"
        "设置为 0 表示不限量。该上限只统计当天新解锁的文件夹；已解锁文件夹不会重复扣积分。",
        reply_markup=kb,
    )
    await callback.answer()


@dp.callback_query(F.data == "settings_daily_unlock_custom")
async def settings_daily_unlock_custom(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    pending_config[callback.from_user.id] = "daily_unlock_limit"
    await callback.message.answer("请发送每日最多解锁的文件夹数量（0–1000；0 表示不限量）。发送「取消设置」可退出。")
    await callback.answer()


@dp.callback_query(F.data == "settings_menu_manage")
async def settings_menu_manage(callback: CallbackQuery, answer_callback: bool = True):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    items = [
        ("my_info", "menu_my_info_text", "menu_show_my_info", "我的信息"),
        ("checkin", "menu_checkin_text", "menu_show_checkin", "每日签到"),
        ("random", "menu_random_text", "menu_show_random", "随机资源"),
        ("buy", "menu_buy_text", "menu_show_buy", "购买积分"),
        ("help", "menu_help_text", "menu_show_help", "用户帮助"),
    ]
    order = [part.strip() for part in (await get_config("menu_order", DEFAULT_CONFIG["menu_order"])).split(",")]
    lines = ["🧩 用户菜单管理", "━━━━━━━━━━━━━━", "菜单显示顺序：" + " → ".join(order)]
    buttons = []
    for item_key, text_key, show_key, title in items:
        label = await get_config(text_key, DEFAULT_CONFIG[text_key])
        visible = await get_config(show_key, "1") == "1"
        lines.append(f"{'🟢 显示' if visible else '⚪️ 隐藏'}｜{title}：{label}")
        buttons.append([
            InlineKeyboardButton(text=f"✏️ 改名：{title}", callback_data=f"settings_menu_label:{text_key}"),
            InlineKeyboardButton(text=("隐藏" if visible else "显示") + f"：{title}", callback_data=f"menu_toggle:{show_key}:{'0' if visible else '1'}"),
        ])
        buttons.append([
            InlineKeyboardButton(text=f"⬆️ {title}", callback_data=f"menu_move:{item_key}:up"),
            InlineKeyboardButton(text=f"⬇️ {title}", callback_data=f"menu_move:{item_key}:down"),
        ])
    buttons.append([InlineKeyboardButton(text="↩️ 返回设置中心", callback_data="settings_home")])
    await callback.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    if answer_callback:
        await callback.answer()


@dp.callback_query(F.data.startswith("settings_menu_label:"))
async def settings_menu_label(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    key = callback.data.split(":", 1)[1]
    if key not in ("menu_my_info_text", "menu_checkin_text", "menu_random_text", "menu_buy_text", "menu_help_text"):
        await callback.answer("菜单设置项无效", show_alert=True)
        return
    pending_config[callback.from_user.id] = key
    await callback.message.answer("请发送新的菜单按钮名称（最多 32 个字符，不能与其他菜单重名）。发送「取消设置」可退出。")
    await callback.answer()


@dp.callback_query(F.data.startswith("menu_move:"))
async def settings_menu_move(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    try:
        _, item_key, direction = callback.data.split(":", 2)
    except ValueError:
        await callback.answer("菜单排序参数无效", show_alert=True)
        return
    allowed = ["my_info", "checkin", "random", "buy", "help"]
    if item_key not in allowed or direction not in ("up", "down"):
        await callback.answer("菜单排序参数无效", show_alert=True)
        return
    order = [part.strip() for part in (await get_config("menu_order", DEFAULT_CONFIG["menu_order"])).split(",")]
    index = order.index(item_key)
    target = index - 1 if direction == "up" else index + 1
    if target < 0 or target >= len(order):
        await callback.answer("已经到达边界", show_alert=True)
        return
    order[index], order[target] = order[target], order[index]
    await set_config("menu_order", ",".join(order))
    await callback.answer("菜单顺序已保存")
    await settings_menu_manage(callback, answer_callback=False)


@dp.callback_query(F.data.startswith("menu_toggle:"))
async def settings_menu_toggle(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    try:
        _, key, value = callback.data.split(":", 2)
    except ValueError:
        await callback.answer("菜单设置参数无效", show_alert=True)
        return
    normalized, error = await validate_config_value(key, value)
    if error:
        await callback.answer(error, show_alert=True)
        return
    await set_config(key, normalized)
    await callback.answer("菜单显示状态已保存")
    await settings_menu_manage(callback, answer_callback=False)


@dp.callback_query(F.data == "admin_db_backup")
async def admin_db_backup(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    import tempfile
    from aiogram.types import FSInputFile
    backup_path = None
    await callback.answer("正在生成数据库备份…")
    try:
        handle = tempfile.NamedTemporaryFile(prefix="yueji-backup-", suffix=".db", delete=False)
        backup_path = handle.name
        handle.close()
        async with aiosqlite.connect(DB_PATH) as source:
            async with aiosqlite.connect(backup_path) as target:
                await source.backup(target)
        filename = "yueji-backup-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".db"
        await bot.send_document(
            callback.from_user.id,
            FSInputFile(backup_path, filename=filename),
            caption="悦己数据库备份。请妥善保管，文件包含用户积分、兑换码及解锁记录。",
        )
        await callback.message.answer("✅ 数据库备份已私信发送给管理员。")
    except Exception as exc:
        await callback.message.answer("❌ 数据库备份失败，请检查机器人私聊权限和磁盘空间。")
        print("Database backup failed:", str(exc)[:250])
    finally:
        if backup_path and os.path.exists(backup_path):
            try:
                os.remove(backup_path)
            except OSError:
                pass


@dp.callback_query(F.data == "settings_browse_limit")
async def settings_browse_limit(callback: CallbackQuery, answer_callback: bool = True):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    minutes = await get_config("browse_minutes", "5")
    scope = await get_config("browse_scope", "all")
    admin_exempt = await get_config("browse_admin_exempt", "1")
    duration_label = "不限时" if minutes == "0" else f"{minutes} 分钟"
    scope_label = "全部资源" if scope == "all" else "仅视频"
    admin_label = "管理员免限时" if admin_exempt == "1" else "管理员也限时"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="3 分钟", callback_data="cfgset:browse_minutes:3"),
            InlineKeyboardButton(text="5 分钟", callback_data="cfgset:browse_minutes:5"),
            InlineKeyboardButton(text="10 分钟", callback_data="cfgset:browse_minutes:10"),
        ],
        [
            InlineKeyboardButton(text="不限时", callback_data="cfgset:browse_minutes:0"),
            InlineKeyboardButton(text="自定义分钟", callback_data="settings_browse_custom"),
        ],
        [
            InlineKeyboardButton(text="限制全部资源", callback_data="browse_config:browse_scope:all"),
            InlineKeyboardButton(text="只限制视频", callback_data="browse_config:browse_scope:videos"),
        ],
        [InlineKeyboardButton(
            text=("管理员免限时：开启" if admin_exempt == "1" else "管理员免限时：关闭"),
            callback_data=f"browse_config:browse_admin_exempt:{'0' if admin_exempt == '1' else '1'}",
        )],
        [InlineKeyboardButton(text="↩️ 返回设置中心", callback_data="settings_home")],
    ])
    await callback.message.edit_text(
        "⏱️ 解锁后浏览时长设置\n━━━━━━━━━━━━━━\n"
        f"当前时长：{duration_label}\n限制范围：{scope_label}\n管理员：{admin_label}\n\n"
        "计时从用户点击「开始浏览资源」时开始。到期后阻止新的受限资源发送，已发送的消息无法撤回。"
        "\n注意：新设置只影响之后开始的浏览会话。",
        reply_markup=kb,
    )
    if answer_callback:
        await callback.answer()


@dp.callback_query(F.data == "settings_browse_custom")
async def settings_browse_custom(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    pending_config[callback.from_user.id] = "browse_minutes"
    await callback.message.answer("请发送浏览时长分钟数（1–1440；发送 0 表示不限时）。发送「取消设置」可取消。")
    await callback.answer()


@dp.callback_query(F.data.startswith("browse_config:"))
async def settings_browse_config(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    try:
        _, key, value = callback.data.split(":", 2)
    except ValueError:
        await callback.answer("设置参数无效", show_alert=True)
        return
    normalized, error = await validate_config_value(key, value)
    if error:
        await callback.answer(error, show_alert=True)
        return
    await set_config(key, normalized)
    await callback.answer("设置已保存")
    await settings_browse_limit(callback, answer_callback=False)


@dp.callback_query(F.data == "settings_random_count")
async def settings_random_count(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True); return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"{n} 个", callback_data=f"cfgset:random_count:{n}") for n in (1, 3, 5, 10)
    ], [InlineKeyboardButton(text="↩️ 返回设置", callback_data="settings_home")]])
    await callback.message.edit_text("🎲 请选择每次随机推送的文件夹数量（1–10）：", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data.startswith("cfgset:"))
async def settings_set_value(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True); return
    _, key, value = callback.data.split(":", 2)
    if key not in ("start_points", "buy_options", "random_count", "source_folder_price", "browse_minutes", "daily_unlock_limit"):
        await callback.answer("不支持的设置项", show_alert=True); return
    normalized, error = await validate_config_value(key, value)
    if error:
        await callback.answer(error, show_alert=True)
        return
    value = normalized
    await set_config(key, value)
    await callback.message.edit_text(f"✅ 设置已保存\n\n{key} = {value}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚙️ 返回设置中心", callback_data="settings_home")]]))
    await callback.answer("保存成功")


@dp.callback_query(F.data.startswith("cfgcheck:"))
async def settings_set_checkin(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True); return
    try:
        _, low, high = callback.data.split(":")
        low_n, high_n = int(low), int(high)
    except (ValueError, TypeError):
        await callback.answer("签到参数无效", show_alert=True)
        return
    if not (0 <= low_n <= high_n <= 100000):
        await callback.answer("签到范围必须为 0–100000，且最低值不能高于最高值", show_alert=True)
        return
    await set_config("checkin_min", str(low_n))
    await set_config("checkin_max", str(high_n))
    await callback.message.edit_text(f"✅ 签到奖励已设置为 {low}–{high} 积分。", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚙️ 返回设置中心", callback_data="settings_home")]]))
    await callback.answer("保存成功")


@dp.callback_query(F.data.startswith("settings_text:"))
async def settings_text_prompt(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True); return
    key = callback.data.split(":", 1)[1]
    if key not in ("welcome_text", "help_text", "announcement_text", "support_url"):
        await callback.answer("文案设置项无效", show_alert=True)
        return
    pending_config[callback.from_user.id] = key
    labels = {
        "welcome_text": "欢迎文案",
        "help_text": "帮助文案",
        "announcement_text": "公告文案（发送「清空」可移除公告）",
        "support_url": "客服链接（发送「清空」可移除客服链接）",
    }
    await callback.message.answer(f"✏️ 请直接发送新的{labels[key]}。\n发送「取消设置」可取消。")
    await callback.answer()


@dp.callback_query(F.data == "settings_welcome_image")
async def settings_welcome_image(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True); return
    pending_config[callback.from_user.id] = "welcome_image_file_id"
    await callback.message.answer("🖼️ 请现在发送一张欢迎页图片（作为照片发送，不要作为文件）。\n发送「取消设置」可取消。")
    await callback.answer()


@dp.callback_query(F.data == "settings_show")
async def settings_show(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True); return
    lines = ["📋 当前设置", "━━━━━━━━━━━━━━"]
    for key in DEFAULT_CONFIG:
        value = await get_config(key, DEFAULT_CONFIG[key])
        if key == "welcome_image_file_id":
            value = "已设置" if value else "未设置"
        elif key in ("welcome_text", "help_text"):
            value = value[:70] + ("…" if len(value) > 70 else "")
        lines.append(f"• {key}: {value}")
    await callback.message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚙️ 返回设置中心", callback_data="settings_home")]]))
    await callback.answer()


@dp.message(Command("add"))
async def cmd_add(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("无权限")
        return
    await message.answer(
        "管理员上传资源：\n"
        "1. 发送：标题|描述|积分价格\n"
        "2. 连续发送图片/视频/文件\n"
        "3. 回复「完成」"
    )


@dp.message(Command("list"))
async def cmd_list(message: Message):
    if not is_admin(message.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, title, price FROM resources ORDER BY id DESC LIMIT 20") as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await message.answer("暂无资源")
        return
    text = "最近资源：\n"
    for rid, title, price in rows:
        text += f"#{rid} {title} ({price}积分)\n"
    text += "\n删除：/del 资源ID"
    await message.answer(text)


@dp.message(Command("del"))
async def cmd_del(message: Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("用法：/del 资源ID")
        return
    try:
        rid = int(args[1])
    except:
        await message.answer("ID必须是数字")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM resources WHERE id = ?", (rid,))
        await db.execute("DELETE FROM unlocks WHERE resource_id = ?", (rid,))
        await db.commit()
    await message.answer(f"已删除资源 #{rid}")


upload_state = {}
pending_config = {}
admin_user_search_queries = {}


@dp.message(F.text.contains("|") & F.from_user.id == ADMIN_ID)
async def start_upload(message: Message):
    parts = message.text.split("|")
    if len(parts) < 3:
        await message.answer("格式：标题|描述|价格")
        return
    title, desc, price_str = parts[0].strip(), parts[1].strip(), parts[2].strip()
    try:
        price = int(price_str)
    except:
        await message.answer("价格必须是数字")
        return
    upload_state[message.from_user.id] = {
        "title": title,
        "description": desc,
        "price": price,
        "files": [],
    }
    await message.answer("已记录，请发送文件，完成后回复「完成」")


@dp.message(F.photo | F.video | F.document)
async def collect_files(message: Message):
    user_id = message.from_user.id
    if is_admin(user_id) and pending_config.get(user_id) == "welcome_image_file_id" and message.photo:
        await set_config("welcome_image_file_id", message.photo[-1].file_id)
        pending_config.pop(user_id, None)
        await message.answer("✅ 欢迎图片已保存！用户下次发送 /start 时即可看到图片。")
        return
    if user_id not in upload_state:
        return
    state = upload_state[user_id]
    if message.photo:
        state["files"].append({"type": "photo", "file_id": message.photo[-1].file_id})
    elif message.video:
        state["files"].append({"type": "video", "file_id": message.video.file_id})
    elif message.document:
        state["files"].append({"type": "document", "file_id": message.document.file_id})
    await message.answer(f"已收到 {len(state['files'])} 个文件")


@dp.message(F.text == "完成")
async def finish_upload(message: Message):
    if message.from_user.id not in upload_state:
        return
    state = upload_state.pop(message.from_user.id)
    files = state["files"]
    img = sum(1 for f in files if f["type"] == "photo")
    vid = sum(1 for f in files if f["type"] == "video")
    doc = sum(1 for f in files if f["type"] == "document")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO resources (title, description, image_count, video_count, file_count, price, file_ids) VALUES (?,?,?,?,?,?,?)",
            (state["title"], state["description"], img, vid, doc, state["price"], json.dumps(files)),
        )
        await db.commit()
    await message.answer(f"资源「{state['title']}」上传成功")


@dp.message(F.text.in_({"取消设置"}) & F.from_user.id == ADMIN_ID)
async def cancel_pending_setting(message: Message):
    if pending_config.pop(message.from_user.id, None):
        await message.answer("已取消本次设置。")
    else:
        await message.answer("当前没有等待输入的设置。")


@dp.message(F.text == "👤 我的信息")
@dp.message(F.text == "我的信息")
async def my_info(message: Message):
    points, spent, unlocked = await get_or_create_user(
        message.from_user.id, message.from_user.username or ""
    )
    text = (
        "👤 我的悦己账户\n━━━━━━━━━━━━━━\n"
        f"🌷 用户名：{message.from_user.username or '未设置'}\n"
        f"💎 当前积分：{points}\n"
        f"🛍️ 累计消费：{spent} 积分\n"
        f"🔓 已解锁资源：{unlocked} 个\n\n"
        "✨ 继续签到或探索资源，开启你的悦己体验。"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="查看已解锁资源", callback_data="view_unlocked")]]
    )
    await message.answer(text, reply_markup=kb)


@dp.message(F.text == "🎁 每日签到")
@dp.message(F.text == "每日签到")
async def daily_checkin(message: Message):
    user_id = message.from_user.id
    await get_or_create_user(user_id, message.from_user.username or "")
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM checkins WHERE user_id = ? AND checkin_date = ?", (user_id, today)
        ) as cursor:
            if await cursor.fetchone():
                await message.answer("🌷 今天已经签到过啦！\n明天再来签到，还可以继续领取积分。")
                return
        cmin = int(await get_config("checkin_min", "1"))
        cmax = int(await get_config("checkin_max", "3"))
        reward = random.randint(cmin, cmax)
        await db.execute("INSERT INTO checkins (user_id, checkin_date) VALUES (?, ?)", (user_id, today))
        await db.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (reward, user_id))
        await db.commit()
    await message.answer(f"🎉 签到成功！\n━━━━━━━━━━━━━━\n💎 本次获得：+{reward} 积分\n🌷 明天记得再来看看哦！")


@dp.message(F.text == "🛍️ 购买积分")
@dp.message(F.text == "购买积分")
async def buy_points(message: Message):
    options = (await get_config("buy_options", "10,30,50,100")).split(",")
    buttons = []
    for opt in options:
        opt = opt.strip()
        if opt.isdigit():
            buttons.append([InlineKeyboardButton(text=f"{opt} 积分", callback_data=f"buy_{opt}")])
    buttons.append([InlineKeyboardButton(text="🎟️ 输入积分兑换码", callback_data="redeem_start")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    points, _, _ = await get_or_create_user(message.from_user.id, message.from_user.username or "")
    await message.answer(f"🛍️ 积分中心\n━━━━━━━━━━━━━━\n💎 当前积分：{points}\n👇 购买后获取兑换码，再点击下方按钮兑换积分。\n\nℹ️ 积分档位仅作参考，购买渠道请以管理员提供的信息为准。", reply_markup=kb)


@dp.message(F.text == "💡 用户帮助")
@dp.message(F.text == "用户帮助")
@dp.message(F.text == "💡 帮助")
@dp.message(F.text == "帮助")
async def help_cmd(message: Message):
    help_text = await get_config("help_text")
    support_url = await get_config("support_url", "")
    if support_url:
        help_text = (help_text or "") + "\n\n🆘 客服：" + support_url
    await message.answer(help_text)


@dp.message(F.text == "🎲 随机资源")
@dp.message(F.text == "随机资源")
async def random_resource(message: Message):
    # Random browsing must use the synced folder library only. Never fall back
    # to the unrelated legacy resources table, which can send stray videos/cards.
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM source_folders WHERE item_count > 0"
        ) as cur:
            row = await cur.fetchone()
    folder_count = int(row[0] or 0) if row else 0

    if folder_count <= 0:
        await message.answer(
            "📭 暂时没有可随机展示的已同步资源文件夹。\n"
            "请稍后重试，或联系管理员在「资源仓库管理」中同步文件夹与文件。"
        )
        return

    if not await choose_random_source_folder(message):
        await message.answer(
            "⚠️ 已找到资源文件夹，但暂时无法生成预览相册。\n"
            "本次不会发送其他资源或随机视频。请确认悦己机器人仍在原仓库频道内且有查看权限，"
            "并检查频道是否开启了内容保护；之后请管理员检查同步记录。"
        )


async def send_resource_files(chat_id: int, file_ids_json: str):
    try:
        files = json.loads(file_ids_json or "[]")
    except:
        files = []
    for f in files:
        fid = f.get("file_id")
        ftype = f.get("type")
        try:
            if ftype == "photo":
                await bot.send_photo(chat_id, fid, protect_content=True)
            elif ftype == "video":
                await bot.send_video(chat_id, fid, protect_content=True)
            elif ftype == "document":
                await bot.send_document(chat_id, fid, protect_content=True)
        except Exception:
            pass


@dp.callback_query(F.data.startswith("unlock_"))
async def unlock_callback(callback: CallbackQuery):
    try:
        rid = int(callback.data.split("_", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("资源编号无效", show_alert=True)
        return
    user_id = callback.from_user.id
    await get_or_create_user(user_id, callback.from_user.username or "")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute("SELECT price, file_ids FROM resources WHERE id = ?", (rid,)) as cursor:
            row = await cursor.fetchone()
        if not row:
            await db.rollback()
            await callback.answer("资源不存在", show_alert=True)
            return
        async with db.execute(
            "SELECT 1 FROM unlocks WHERE user_id=? AND resource_id=?",
            (user_id, rid),
        ) as cursor:
            existing = await cursor.fetchone()
        if existing:
            await db.rollback()
            await callback.answer("你已解锁过该资源，不会重复扣积分。", show_alert=True)
            return
        price, file_ids = row
        async with db.execute("SELECT points FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user_row = await cursor.fetchone()
        if not user_row or int(user_row[0] or 0) < int(price or 0):
            await db.rollback()
            await callback.answer("积分不足", show_alert=True)
            return
        await db.execute(
            "UPDATE users SET points = points - ?, total_spent = total_spent + ?, unlocked_count = unlocked_count + 1 WHERE user_id = ?",
            (price, price, user_id),
        )
        await db.execute(
            "INSERT INTO unlocks (user_id, resource_id, unlocked_at) VALUES (?, ?, datetime('now'))",
            (user_id, rid),
        )
        await db.commit()
    await callback.answer("解锁成功")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer("🎉 解锁成功！\n━━━━━━━━━━━━━━\n📦 正在为你发送资源，请稍候…")
    await send_resource_files(callback.message.chat.id, file_ids)


@dp.callback_query(F.data == "view_unlocked")
async def view_unlocked(callback: CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT r.title, r.file_ids FROM unlocks u JOIN resources r ON u.resource_id = r.id WHERE u.user_id = ?",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await callback.message.answer("暂无已解锁资源。")
    else:
        for title, file_ids in rows:
            await callback.message.answer(f"【{title}】")
            await send_resource_files(callback.message.chat.id, file_ids)
    await callback.answer()


redeem_state = set()


@dp.callback_query(F.data == "redeem_start")
async def redeem_start(callback: CallbackQuery):
    redeem_state.add(callback.from_user.id)
    await callback.message.answer("🎟️ 请发送你的积分兑换码。\n发送「取消兑换」可退出。")
    await callback.answer()


@dp.callback_query(F.data.startswith("buy_"))
async def buy_callback(callback: CallbackQuery):
    await callback.answer("请按管理员提供的购买渠道获取兑换码，再点击「输入积分兑换码」。", show_alert=True)


async def dispatch_custom_menu(message: Message):
    text = (message.text or "").strip()
    items = [
        ("menu_show_my_info", "menu_my_info_text", my_info),
        ("menu_show_checkin", "menu_checkin_text", daily_checkin),
        ("menu_show_random", "menu_random_text", random_resource),
        ("menu_show_buy", "menu_buy_text", buy_points),
        ("menu_show_help", "menu_help_text", help_cmd),
    ]
    for show_key, text_key, handler in items:
        label = await get_config(text_key, DEFAULT_CONFIG[text_key])
        visible = await get_config(show_key, "1") == "1"
        if visible and text == label:
            await handler(message)
            return True
    return False


@dp.message(F.from_user.id == ADMIN_ID)
async def handle_pending_admin_text(message: Message):
    user_id = message.from_user.id
    if user_id in redeem_state:
        await redeem_code_message(message)
        return
    key = pending_config.get(user_id)
    if not key or key == "welcome_image_file_id":
        if not key:
            await dispatch_custom_menu(message)
        return
    if message.text == "取消设置":
        pending_config.pop(user_id, None)
        await message.answer("已取消本次设置。")
        return
    if key == "admin_user_search":
        query = (message.text or "").strip().lstrip("@")
        if not query or len(query) > 64:
            await message.answer("请输入有效的用户 ID 或用户名（最多 64 个字符）。")
            return
        admin_user_search_queries[user_id] = query
        pending_config.pop(user_id, None)
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """SELECT user_id, username, points, banned FROM users
                   WHERE CAST(user_id AS TEXT)=? OR username LIKE ?
                   ORDER BY rowid DESC LIMIT 10""",
                (query, f"%{query}%"),
            ) as cur:
                rows = await cur.fetchall()
            async with db.execute(
                """SELECT COUNT(*) FROM users
                   WHERE CAST(user_id AS TEXT)=? OR username LIKE ?""",
                (query, f"%{query}%"),
            ) as cur:
                count_row = await cur.fetchone()
        lines = [
            "🔍 用户搜索结果",
            "━━━━━━━━━━━━━━",
            f"匹配用户：{int(count_row[0] or 0)}（搜索：{query}）",
            "最多显示 10 位，可增减积分或封禁/解封。",
            "",
        ]
        buttons = []
        for uid, username, points, banned in rows:
            label = (("@" + username) if username else str(uid))[:24]
            lines.append(f"• {label}（ID {uid}）：{int(points or 0)} 积分｜{'🚫 已封禁' if banned else '🟢 正常'}")
            buttons.append([
                InlineKeyboardButton(text="➕10", callback_data=f"admin_points:{uid}:10"),
                InlineKeyboardButton(text="➕50", callback_data=f"admin_points:{uid}:50"),
                InlineKeyboardButton(text="➖10", callback_data=f"admin_points:{uid}:-10"),
                InlineKeyboardButton(text=("🔓 解封" if banned else "🚫 封禁"), callback_data=f"admin_ban:{uid}:{0 if banned else 1}"),
            ])
        if not rows:
            lines.append("没有找到匹配用户。")
        buttons.append([InlineKeyboardButton(text="🔍 重新搜索", callback_data="admin_user_search")])
        buttons.append([InlineKeyboardButton(text="📋 最近用户", callback_data="admin_users_clear_search")])
        await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        return
    if key == "buy_options":
        normalized, error = await validate_config_value("buy_options", message.text or "")
        if error:
            await message.answer(error + "\n请重新发送档位列表，或发送「取消设置」。")
            return
        await set_config("buy_options", normalized)
        pending_config.pop(user_id, None)
        await message.answer("✅ 积分档位已保存：" + normalized)
        return
    if key in ("browse_minutes", "daily_unlock_limit"):
        normalized, error = await validate_config_value(key, message.text or "")
        if error:
            await message.answer(error + "\n请重新发送数字，或发送「取消设置」。")
            return
        await set_config(key, normalized)
        pending_config.pop(user_id, None)
        if key == "browse_minutes":
            result_label = "不限时" if normalized == "0" else normalized + " 分钟"
            await message.answer("✅ 浏览时长已保存：" + result_label)
        else:
            result_label = "不限量" if normalized == "0" else "每天最多 " + normalized + " 个文件夹"
            await message.answer("✅ 每日解锁上限已保存：" + result_label)
        return
    if key == "source_repo_id":
        repo_id = (message.text or "").strip()
        if not repo_id.lstrip("-").isdigit() or len(repo_id) < 5:
            await message.answer("频道 ID 格式不正确，请发送数字频道 ID（通常以 -100 开头），或发送「取消设置」。")
            return
        await set_config("source_repo_id", repo_id)
        pending_config.pop(user_id, None)
        await message.answer(f"✅ 已保存原仓库频道 ID：{repo_id}\n请到「📚 资源仓库管理」点击「同步文件夹与文件」。")
        return
    if key in ("menu_my_info_text", "menu_checkin_text", "menu_random_text", "menu_buy_text", "menu_help_text"):
        normalized, error = await validate_config_value(key, message.text or "")
        if error:
            await message.answer(error + "\n请重新发送名称，或发送「取消设置」。")
            return
        await set_config(key, normalized)
        pending_config.pop(user_id, None)
        await message.answer("✅ 菜单名称已保存。用户下次发送 /start 后会看到新名称。")
        return
    if key not in ("welcome_text", "help_text", "announcement_text", "support_url"):
        return
    raw_value = (message.text or "").strip()
    if key in ("announcement_text", "support_url") and raw_value in ("清空", "关闭", "无"):
        raw_value = ""
    normalized, error = await validate_config_value(key, raw_value)
    if error:
        await message.answer(error + "\n请重新发送，或发送「取消设置」。")
        return
    await set_config(key, normalized)
    pending_config.pop(user_id, None)
    if key == "support_url":
        result = "已清除客服链接。" if not normalized else "客服链接已保存。"
    elif key == "announcement_text":
        result = "已清除公告。" if not normalized else "公告已保存，用户下次发送 /start 时会看到。"
    else:
        result = "文案已保存！下次用户打开对应页面时就会使用新文案。"
    await message.answer("✅ " + result)


@dp.message(F.text)
async def redeem_code_message(message: Message):
    user_id = message.from_user.id
    if message.text == "取消兑换" and user_id in redeem_state:
        redeem_state.discard(user_id)
        await message.answer("已取消兑换。")
        return
    if user_id not in redeem_state:
        await dispatch_custom_menu(message)
        return
    code = (message.text or "").strip().upper().replace(" ", "")
    if not code or len(code) > 40:
        await message.answer("兑换码格式不正确，请重新发送，或发送「取消兑换」。")
        return
    await get_or_create_user(user_id, message.from_user.username or "")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT points FROM redemption_codes WHERE code = ? AND used_by IS NULL",
            (code,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            await db.rollback()
            redeem_state.discard(user_id)
            await message.answer("❌ 兑换码无效或已被使用，请检查后重新获取新的兑换码。")
            return
        points = int(row[0])
        cursor = await db.execute(
            "UPDATE redemption_codes SET used_by = ?, used_at = datetime('now') WHERE code = ? AND used_by IS NULL",
            (user_id, code),
        )
        if cursor.rowcount != 1:
            await db.rollback()
            redeem_state.discard(user_id)
            await message.answer("❌ 该兑换码刚刚已被兑换，请获取新的兑换码。")
            return
        await db.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (points, user_id))
        await db.commit()
    redeem_state.discard(user_id)
    new_points, _, _ = await get_or_create_user(user_id, message.from_user.username or "")
    await message.answer(
        f"🎉 兑换成功！\n━━━━━━━━━━━━━━\n🎟️ 兑换积分：+{points}\n💎 当前余额：{new_points} 积分"
    )


async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
