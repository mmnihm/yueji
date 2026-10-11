import asyncio
import os
import random
import json
from datetime import date

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from dotenv import load_dotenv

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
    "welcome_text": "欢迎使用悦己分享机器人\n请选择下方按钮操作。",
    "help_text": "使用帮助\n\n• 新用户注册赠送积分\n• 每日签到可获得积分\n• 点击「随机资源」可预览并解锁\n• 解锁后会直接发送文件\n• 购买积分请点击对应按钮",
}


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                points INTEGER DEFAULT 10,
                total_spent INTEGER DEFAULT 0,
                unlocked_count INTEGER DEFAULT 0
            )
        """)
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
        for k, v in DEFAULT_CONFIG.items():
            await db.execute(
                "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", (k, v)
            )
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
    return ADMIN_ID and user_id == ADMIN_ID


def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="我的信息"), KeyboardButton(text="每日签到")],
            [KeyboardButton(text="购买积分"), KeyboardButton(text="帮助")],
            [KeyboardButton(text="随机资源")],
        ],
        resize_keyboard=True,
    )


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await get_or_create_user(message.from_user.id, message.from_user.username or "")
    welcome = await get_config("welcome_text")
    await message.answer(welcome, reply_markup=main_keyboard())


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
    if key not in DEFAULT_CONFIG:
        await message.answer("无效配置键")
        return
    await set_config(key, value)
    await message.answer(f"已更新 {key} = {value}")


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
    if message.from_user.id not in upload_state:
        return
    state = upload_state[message.from_user.id]
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


@dp.message(F.text == "我的信息")
async def my_info(message: Message):
    points, spent, unlocked = await get_or_create_user(
        message.from_user.id, message.from_user.username or ""
    )
    text = (
        f"用户名：{message.from_user.username or '未设置'}\n"
        f"当前积分：{points}\n"
        f"累计消费：{spent}\n"
        f"已解锁资源：{unlocked} 个"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="查看已解锁资源", callback_data="view_unlocked")]]
    )
    await message.answer(text, reply_markup=kb)


@dp.message(F.text == "每日签到")
async def daily_checkin(message: Message):
    user_id = message.from_user.id
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM checkins WHERE user_id = ? AND checkin_date = ?", (user_id, today)
        ) as cursor:
            if await cursor.fetchone():
                await message.answer("今天已经签到过了。")
                return
        cmin = int(await get_config("checkin_min", "1"))
        cmax = int(await get_config("checkin_max", "3"))
        reward = random.randint(cmin, cmax)
        await db.execute("INSERT INTO checkins (user_id, checkin_date) VALUES (?, ?)", (user_id, today))
        await db.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (reward, user_id))
        await db.commit()
    await message.answer(f"签到成功！获得 {reward} 积分。")


@dp.message(F.text == "购买积分")
async def buy_points(message: Message):
    options = (await get_config("buy_options", "10,30,50,100")).split(",")
    buttons = []
    for opt in options:
        opt = opt.strip()
        if opt.isdigit():
            buttons.append([InlineKeyboardButton(text=f"{opt} 积分", callback_data=f"buy_{opt}")])
    buttons.append([InlineKeyboardButton(text="自定义", callback_data="buy_custom")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    points, _, _ = await get_or_create_user(message.from_user.id, message.from_user.username or "")
    await message.answer(f"当前积分：{points}\n请选择购买档位：", reply_markup=kb)


@dp.message(F.text == "帮助")
async def help_cmd(message: Message):
    help_text = await get_config("help_text")
    await message.answer(help_text)


@dp.message(F.text == "随机资源")
async def random_resource(message: Message):
    user_id = message.from_user.id
    points, _, _ = await get_or_create_user(user_id, message.from_user.username or "")
    count = int(await get_config("random_count", "3"))
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            f"SELECT id, title, description, image_count, video_count, file_count, price FROM resources ORDER BY RANDOM() LIMIT {count}"
        ) as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await message.answer("暂无资源。")
        return
    for row in rows:
        rid, title, desc, img, vid, files, price = row
        text = (
            f"【{title}】\n{desc}\n"
            f"图片：{img} | 视频：{vid} | 文件：{files}\n"
            f"解锁需要：{price} 积分\n你当前剩余：{points} 积分"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="解锁", callback_data=f"unlock_{rid}")]]
        )
        await message.answer(text, reply_markup=kb)


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
    rid = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT price, file_ids FROM resources WHERE id = ?", (rid,)) as cursor:
            row = await cursor.fetchone()
        if not row:
            await callback.answer("资源不存在")
            return
        price, file_ids = row
        async with db.execute("SELECT points FROM users WHERE user_id = ?", (user_id,)) as cursor:
            points = (await cursor.fetchone())[0]
        if points < price:
            await callback.answer("积分不足", show_alert=True)
            return
        await db.execute(
            "UPDATE users SET points = points - ?, total_spent = total_spent + ?, unlocked_count = unlocked_count + 1 WHERE user_id = ?",
            (price, price, user_id),
        )
        await db.execute(
            "INSERT OR IGNORE INTO unlocks (user_id, resource_id, unlocked_at) VALUES (?, ?, datetime('now'))",
            (user_id, rid),
        )
        await db.commit()
    await callback.answer("解锁成功")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("解锁成功，正在发送文件...")
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


@dp.callback_query(F.data.startswith("buy_"))
async def buy_callback(callback: CallbackQuery):
    await callback.answer("支付功能待接入", show_alert=True)


async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
