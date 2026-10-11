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
    FSInputFile,
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
        # 兼容旧表，如果没有 file_ids 列则添加
        try:
            await db.execute("ALTER TABLE resources ADD COLUMN file_ids TEXT DEFAULT '[]'")
        except:
            pass
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
        await db.commit()


async def get_or_create_user(user_id: int, username: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT points, total_spent, unlocked_count FROM users WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            await db.execute(
                "INSERT INTO users (user_id, username, points) VALUES (?, ?, 10)",
                (user_id, username),
            )
            await db.commit()
            return 10, 0, 0
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
    await message.answer(
        "欢迎使用悦己分享机器人\n请选择下方按钮操作。",
        reply_markup=main_keyboard(),
    )


@dp.message(Command("add"))
async def cmd_add(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("无权限")
        return
    await message.answer(
        "管理员上传资源说明：\n"
        "1. 先发文字：标题|描述|积分价格\n"
        "   例如：示例资源|这是合规资源描述|2\n"
        "2. 然后连续发送图片/视频/文件（可多个）\n"
        "3. 发送完成后回复「完成」结束上传\n"
        "现在请先发送文字信息。"
    )


# 临时存储上传状态
upload_state = {}


@dp.message(F.text.contains("|") & F.from_user.id == ADMIN_ID)
async def start_upload(message: Message):
    parts = message.text.split("|")
    if len(parts) < 3:
        await message.answer("格式错误，请用：标题|描述|价格")
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
    await message.answer("已记录信息，请连续发送文件（图片/视频/文档），发完回复「完成」")


@dp.message(F.photo | F.video | F.document)
async def collect_files(message: Message):
    if message.from_user.id not in upload_state:
        return
    state = upload_state[message.from_user.id]
    if message.photo:
        file_id = message.photo[-1].file_id
        state["files"].append({"type": "photo", "file_id": file_id})
    elif message.video:
        file_id = message.video.file_id
        state["files"].append({"type": "video", "file_id": file_id})
    elif message.document:
        file_id = message.document.file_id
        state["files"].append({"type": "document", "file_id": file_id})
    await message.answer(f"已收到文件，当前共 {len(state['files'])} 个")


@dp.message(F.text == "完成")
async def finish_upload(message: Message):
    if message.from_user.id not in upload_state:
        return
    state = upload_state.pop(message.from_user.id)
    files = state["files"]
    img_count = sum(1 for f in files if f["type"] == "photo")
    vid_count = sum(1 for f in files if f["type"] == "video")
    file_count = sum(1 for f in files if f["type"] == "document")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO resources (title, description, image_count, video_count, file_count, price, file_ids)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                state["title"],
                state["description"],
                img_count,
                vid_count,
                file_count,
                state["price"],
                json.dumps(files),
            ),
        )
        await db.commit()
    await message.answer(f"资源「{state['title']}」上传成功，共 {len(files)} 个文件。")


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
        inline_keyboard=[
            [InlineKeyboardButton(text="查看已解锁资源", callback_data="view_unlocked")]
        ]
    )
    await message.answer(text, reply_markup=kb)


@dp.message(F.text == "每日签到")
async def daily_checkin(message: Message):
    user_id = message.from_user.id
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM checkins WHERE user_id = ? AND checkin_date = ?",
            (user_id, today),
        ) as cursor:
            exists = await cursor.fetchone()
        if exists:
            await message.answer("今天已经签到过了。")
            return
        reward = random.randint(1, 3)
        await db.execute(
            "INSERT INTO checkins (user_id, checkin_date) VALUES (?, ?)",
            (user_id, today),
        )
        await db.execute(
            "UPDATE users SET points = points + ? WHERE user_id = ?",
            (reward, user_id),
        )
        await db.commit()
    await message.answer(f"签到成功！获得 {reward} 积分。")


@dp.message(F.text == "购买积分")
async def buy_points(message: Message):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="10 积分", callback_data="buy_10")],
            [InlineKeyboardButton(text="30 积分", callback_data="buy_30")],
            [InlineKeyboardButton(text="50 积分", callback_data="buy_50")],
            [InlineKeyboardButton(text="100 积分", callback_data="buy_100")],
            [InlineKeyboardButton(text="自定义", callback_data="buy_custom")],
        ]
    )
    points, _, _ = await get_or_create_user(message.from_user.id, message.from_user.username or "")
    await message.answer(f"当前积分：{points}\n请选择购买档位：", reply_markup=kb)


@dp.message(F.text == "帮助")
async def help_cmd(message: Message):
    text = (
        "使用帮助\n\n"
        "• 新用户注册赠送 10 积分\n"
        "• 每日签到可获得 1-3 积分\n"
        "• 点击「随机资源」可预览并解锁\n"
        "• 解锁后会直接发送文件，也可在「我的信息」中重新查看\n"
        "• 购买积分请点击对应按钮"
    )
    await message.answer(text)


@dp.message(F.text == "随机资源")
async def random_resource(message: Message):
    user_id = message.from_user.id
    points, _, _ = await get_or_create_user(user_id, message.from_user.username or "")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, title, description, image_count, video_count, file_count, price FROM resources ORDER BY RANDOM() LIMIT 3"
        ) as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await message.answer("暂无资源。")
        return
    for row in rows:
        rid, title, desc, img, vid, files, price = row
        text = (
            f"【{title}】\n"
            f"{desc}\n"
            f"图片：{img} | 视频：{vid} | 文件：{files}\n"
            f"解锁需要：{price} 积分\n"
            f"你当前剩余：{points} 积分"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="解锁", callback_data=f"unlock_{rid}")]
            ]
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
        if ftype == "photo":
            await bot.send_photo(chat_id, fid)
        elif ftype == "video":
            await bot.send_video(chat_id, fid)
        elif ftype == "document":
            await bot.send_document(chat_id, fid)


@dp.callback_query(F.data.startswith("unlock_"))
async def unlock_callback(callback: CallbackQuery):
    rid = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT price, file_ids FROM resources WHERE id = ?", (rid,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            await callback.answer("资源不存在")
            return
        price, file_ids = row
        async with db.execute(
            "SELECT points FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
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
            """
            SELECT r.id, r.title, r.file_ids FROM unlocks u
            JOIN resources r ON u.resource_id = r.id
            WHERE u.user_id = ?
            """,
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await callback.message.answer("暂无已解锁资源。")
    else:
        for rid, title, file_ids in rows:
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
