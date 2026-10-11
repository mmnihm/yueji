import asyncio
import os
import random
import secrets
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
    "welcome_text": "🌷 欢迎来到「悦己」\n━━━━━━━━━━━━━━\n✨ 发现喜欢的内容，解锁专属资源\n🎁 每日签到领取积分\n🎲 随机探索精选资源\n👤 随时查看个人积分与解锁记录\n\n👇 请使用下方菜单开始体验",
    "help_text": "💡 悦己使用指南\n━━━━━━━━━━━━━━\n👤 我的信息：查看积分与解锁记录\n🎁 每日签到：每天领取随机积分奖励\n🎲 随机资源：浏览资源并使用积分解锁\n🛍️ 购买积分：查看积分档位并获取兑换码\n🎟️ 积分兑换：在购买积分页面输入兑换码\n\n🔐 解锁成功后，机器人会发送对应资源。\n如有疑问，请联系管理员。",
    "welcome_image_file_id": "",
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


def main_keyboard(user_id: int = 0):
    rows = [
        [KeyboardButton(text="👤 我的信息"), KeyboardButton(text="🎁 每日签到"), KeyboardButton(text="🎲 随机资源")],
        [KeyboardButton(text="🛍️ 购买积分"), KeyboardButton(text="💡 用户帮助")],
    ]
    if is_admin(user_id):
        rows.append([KeyboardButton(text="⚙️ 设置中心")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await get_or_create_user(message.from_user.id, message.from_user.username or "")
    welcome = await get_config("welcome_text") or DEFAULT_CONFIG["welcome_text"]
    image_id = await get_config("welcome_image_file_id", "")
    keyboard = main_keyboard(message.from_user.id)
    if image_id:
        try:
            if len(welcome) <= 1024:
                await message.answer_photo(image_id, caption=welcome, reply_markup=keyboard)
            else:
                await message.answer_photo(image_id, caption="🌷 欢迎来到「悦己」", reply_markup=keyboard)
                await message.answer(welcome)
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
    if key not in DEFAULT_CONFIG:
        await message.answer("无效配置键")
        return
    await set_config(key, value)
    await message.answer(f"已更新 {key} = {value}")


@dp.message(F.text == "⚙️ 设置中心")
async def settings_center(message: Message):
    if not is_admin(message.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 新用户赠送积分", callback_data="settings_start_points")],
        [InlineKeyboardButton(text="📅 每日签到奖励", callback_data="settings_checkin")],
        [InlineKeyboardButton(text="🛍️ 积分购买档位", callback_data="settings_buy_options")],
        [InlineKeyboardButton(text="🎲 随机资源数量", callback_data="settings_random_count")],
        [InlineKeyboardButton(text="✏️ 修改欢迎文案", callback_data="settings_text:welcome_text"), InlineKeyboardButton(text="💡 修改帮助文案", callback_data="settings_text:help_text")],
        [InlineKeyboardButton(text="🖼️ 设置欢迎图片", callback_data="settings_welcome_image")],
        [InlineKeyboardButton(text="🎟️ 兑换码管理", callback_data="codes_manage")],
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
        [InlineKeyboardButton(text="🎲 随机资源数量", callback_data="settings_random_count")],
        [InlineKeyboardButton(text="✏️ 修改欢迎文案", callback_data="settings_text:welcome_text"), InlineKeyboardButton(text="💡 修改帮助文案", callback_data="settings_text:help_text")],
        [InlineKeyboardButton(text="🖼️ 设置欢迎图片", callback_data="settings_welcome_image")],
        [InlineKeyboardButton(text="🎟️ 兑换码管理", callback_data="codes_manage")],
        [InlineKeyboardButton(text="📋 查看当前设置", callback_data="settings_show")],
    ])
    await callback.message.edit_text("⚙️ 悦己设置中心\n━━━━━━━━━━━━━━\n请选择要调整的项目。", reply_markup=kb)
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
        "🎟️ 兑换码管理\\n━━━━━━━━━━━━━━\\n先选择每个兑换码对应的积分，再选择生成数量。\\n每个兑换码只能成功兑换一次。",
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
    await callback.message.edit_text(f"🎟️ 每个兑换码价值 {points} 积分。\\n请选择本次生成数量：", reply_markup=kb)
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
    body = "\\n".join(generated)
    await callback.message.answer(
        f"✅ 已生成 {quantity} 个兑换码\\n每个兑换码：{points} 积分\\n\\n{body}\\n\\n请妥善保管并只发给已完成付款的用户。兑换码可使用一次。"
    )
    await callback.answer("生成成功")
    await callback.message.edit_reply_markup(reply_markup=None)


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
        [InlineKeyboardButton(text="↩️ 返回设置", callback_data="settings_home")],
    ])
    await callback.message.edit_text("🛍️ 请选择积分购买档位。提示：当前购买按钮仅为档位展示，真实支付尚未接入。", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data == "settings_random_count")
async def settings_random_count(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True); return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"{n} 个", callback_data=f"cfgset:random_count:{n}") for n in (1, 3, 5, 10)
    ], [InlineKeyboardButton(text="↩️ 返回设置", callback_data="settings_home")]])
    await callback.message.edit_text("🎲 请选择每次随机推送的资源数量：", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data.startswith("cfgset:"))
async def settings_set_value(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True); return
    _, key, value = callback.data.split(":", 2)
    if key not in ("start_points", "buy_options", "random_count"):
        await callback.answer("不支持的设置项", show_alert=True); return
    if key == "buy_options" and not all(x.strip().isdigit() for x in value.split(",")):
        await callback.answer("档位格式不正确", show_alert=True); return
    if key != "buy_options" and not value.isdigit():
        await callback.answer("请输入数字", show_alert=True); return
    await set_config(key, value)
    await callback.message.edit_text(f"✅ 设置已保存\n\n{key} = {value}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚙️ 返回设置中心", callback_data="settings_home")]]))
    await callback.answer("保存成功")


@dp.callback_query(F.data.startswith("cfgcheck:"))
async def settings_set_checkin(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True); return
    _, low, high = callback.data.split(":")
    await set_config("checkin_min", low)
    await set_config("checkin_max", high)
    await callback.message.edit_text(f"✅ 签到奖励已设置为 {low}–{high} 积分。", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚙️ 返回设置中心", callback_data="settings_home")]]))
    await callback.answer("保存成功")


@dp.callback_query(F.data.startswith("settings_text:"))
async def settings_text_prompt(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True); return
    key = callback.data.split(":", 1)[1]
    pending_config[callback.from_user.id] = key
    label = "欢迎文案" if key == "welcome_text" else "帮助文案"
    await callback.message.answer(f"✏️ 请直接发送新的{label}。\n发送「取消设置」可取消。")
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
    await message.answer(help_text)


@dp.message(F.text == "🎲 随机资源")
@dp.message(F.text == "随机资源")
async def random_resource(message: Message):
    user_id = message.from_user.id
    points, _, _ = await get_or_create_user(user_id, message.from_user.username or "")
    count = int(await get_config("random_count", "3"))
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            f"SELECT id, title, description, image_count, video_count, file_count, price, file_ids FROM resources ORDER BY RANDOM() LIMIT {count}"
        ) as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await message.answer("📭 暂时还没有可展示的资源。\n请稍后再来探索，感谢你的耐心。")
        return
    for row in rows:
        rid, title, desc, img, vid, files, price, file_ids_json = row
        text = (
            f"🌷 {title}\n━━━━━━━━━━━━━━\n"
            f"📝 {desc or '暂无简介'}\n\n"
            f"🖼️ 图片：{img}  |  🎬 视频：{vid}  |  📄 文件：{files}\n"
            f"🔓 解锁价格：{price} 积分\n💎 你的余额：{points} 积分\n\n"
            "👇 点击下方按钮解锁资源"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=f"🔓 解锁（{price} 积分）", callback_data=f"unlock_{rid}")]]
        )
        try:
            resource_files = json.loads(file_ids_json or "[]")
        except Exception:
            resource_files = []
        preview = next((item.get("file_id") for item in resource_files if item.get("type") == "photo" and item.get("file_id")), None)
        if preview and len(text) <= 1024:
            try:
                await message.answer_photo(preview, caption=text, reply_markup=kb)
                continue
            except Exception:
                pass
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
    await callback.message.answer("🎟️ 请发送你的积分兑换码。\\n发送「取消兑换」可退出。")
    await callback.answer()


@dp.callback_query(F.data.startswith("buy_"))
async def buy_callback(callback: CallbackQuery):
    await callback.answer("请按管理员提供的购买渠道获取兑换码，再点击「输入积分兑换码」。", show_alert=True)


@dp.message(F.from_user.id == ADMIN_ID)
async def handle_pending_admin_text(message: Message):
    user_id = message.from_user.id
    key = pending_config.get(user_id)
    if not key or key == "welcome_image_file_id":
        return
    if message.text == "取消设置":
        pending_config.pop(user_id, None)
        await message.answer("已取消本次设置。")
        return
    if key not in ("welcome_text", "help_text"):
        return
    if not message.text or len(message.text) > 3500:
        await message.answer("文案不能为空，且请控制在 3500 字以内。")
        return
    await set_config(key, message.text)
    pending_config.pop(user_id, None)
    await message.answer("✅ 文案已保存！\n下次用户打开对应页面时就会使用新文案。")


@dp.message(F.text)
async def redeem_code_message(message: Message):
    user_id = message.from_user.id
    if message.text == "取消兑换" and user_id in redeem_state:
        redeem_state.discard(user_id)
        await message.answer("已取消兑换。")
        return
    if user_id not in redeem_state:
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
        f"🎉 兑换成功！\\n━━━━━━━━━━━━━━\\n🎟️ 兑换积分：+{points}\\n💎 当前余额：{new_points} 积分"
    )


async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
