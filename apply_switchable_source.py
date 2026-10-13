#!/usr/bin/env python3
"""一键把「可切换源仓库」写进现有代码。在 /root/yueji 运行。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def patch_forum_import_service() -> None:
    path = ROOT / "forum_import_service.py"
    if not path.exists():
        sys.exit("缺少 forum_import_service.py")
    text = path.read_text(encoding="utf-8")
    if "async def get_source_chat_id" in text and "source_chat_id = await get_source_chat_id" in text:
        print("forum_import_service.py 已支持可切换源，跳过")
        return

    m = re.search(
        r'FORUM_CHAT_ID\s*=\s*int\(\s*os\.getenv\(\s*["\']FORUM_CHAT_ID["\'][^)]*\)\s*\)',
        text,
    )
    if not m:
        sys.exit("无法定位 FORUM_CHAT_ID = int(os.getenv(...))，请检查文件")

    inject = '''
_ENV_FORUM = (os.getenv("FORUM_CHAT_ID") or "").strip()
_DEFAULT_FORUM_CHAT_ID = int(_ENV_FORUM) if _ENV_FORUM.lstrip("-").isdigit() else 0


async def get_source_chat_id(db_path=None):
    """优先读数据库 config（机器人后台可改），其次 .env，没有则报错。"""
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
    if _DEFAULT_FORUM_CHAT_ID:
        return int(_DEFAULT_FORUM_CHAT_ID)
    raise RuntimeError(
        "未配置源仓库。请在机器人「资源仓库扫描」→「更换源仓库 ID」设置后再扫描。"
    )


async def set_source_chat_id(db_path, chat_id: int):
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


FORUM_CHAT_ID = _DEFAULT_FORUM_CHAT_ID  # 兼容旧引用；实际扫描用 get_source_chat_id
'''
    text = text[: m.start()] + inject + text[m.end() :]

    anchor = "api_id, api_hash = load_api_creds()\n    path = Path(db_path) if db_path else DB_PATH"
    if "source_chat_id = await get_source_chat_id" not in text:
        if anchor not in text:
            sys.exit("无法定位 run_forum_import 内 path 赋值")
        text = text.replace(
            anchor,
            anchor + "\n    source_chat_id = await get_source_chat_id(path)",
            1,
        )
    text = text.replace("get_entity(FORUM_CHAT_ID)", "get_entity(source_chat_id)")
    text = text.replace("str(FORUM_CHAT_ID)", "str(source_chat_id)")

    needle = "INSERT OR REPLACE INTO config(key,value) VALUES('source_repo_id',?)"
    if needle in text and "forum_chat_id" not in text[text.find(needle) : text.find(needle) + 200]:
        text = text.replace(
            'await db.execute("INSERT OR REPLACE INTO config(key,value) VALUES(\'source_repo_id\',?)", (str(source_chat_id),))',
            'await db.execute("INSERT OR REPLACE INTO config(key,value) VALUES(\'source_repo_id\',?)", (str(source_chat_id),))\n'
            '                await db.execute("INSERT OR REPLACE INTO config(key,value) VALUES(\'forum_chat_id\',?)", (str(source_chat_id),))',
            1,
        )

    compile(text, "forum_import_service.py", "exec")
    path.write_text(text, encoding="utf-8")
    print("OK: forum_import_service.py → 扫描使用数据库中的源 ID")


def patch_bot_forum_ui() -> None:
    path = ROOT / "bot_forum_ui.py"
    if not path.exists():
        sys.exit("缺少 bot_forum_ui.py")
    text = path.read_text(encoding="utf-8")
    if "forum_set_source" in text and "set_source_chat_id" in text:
        print("bot_forum_ui.py 已有更换源仓库入口，跳过")
        return

    if "get_source_chat_id" not in text:
        text = text.replace(
            "from forum_import_service import run_forum_import, is_import_running",
            "from forum_import_service import (\n"
            "    run_forum_import, is_import_running,\n"
            "    get_source_chat_id, set_source_chat_id,\n"
            ")",
            1,
        )
        if "get_source_chat_id" not in text:
            text = "from forum_import_service import get_source_chat_id, set_source_chat_id\n" + text

    if "_pending_source" not in text:
        text = text.replace(
            "def register_forum_ui(",
            "_pending_source = {}\n\n\ndef register_forum_ui(",
            1,
        )

    if "forum_set_source" not in text:
        old_kb = 'kb = InlineKeyboardMarkup(inline_keyboard=[\n            [InlineKeyboardButton(text="🔍 仅刷新话题列表"'
        new_kb = (
            'kb = InlineKeyboardMarkup(inline_keyboard=[\n'
            '            [InlineKeyboardButton(text="🔄 更换源仓库 ID", callback_data="forum_set_source")],\n'
            '            [InlineKeyboardButton(text="🔍 仅刷新话题列表"'
        )
        if old_kb in text:
            text = text.replace(old_kb, new_kb, 1)
        else:
            text = text.replace(
                'inline_keyboard=[\n            [InlineKeyboardButton(text="🔍 仅刷新话题列表", callback_data="forum_do:sync")]',
                'inline_keyboard=[\n            [InlineKeyboardButton(text="🔄 更换源仓库 ID", callback_data="forum_set_source")],\n'
                '            [InlineKeyboardButton(text="🔍 仅刷新话题列表", callback_data="forum_do:sync")]',
                1,
            )

    if '@dp.callback_query(F.data == "forum_set_source")' not in text:
        handlers = '''

    @dp.callback_query(F.data == "forum_set_source")
    async def forum_set_source(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        _pending_source[callback.from_user.id] = True
        try:
            cur = await get_source_chat_id(db_path)
            cur_s = str(cur)
        except Exception:
            cur_s = "未设置"
        await callback.message.answer(
            f"当前源仓库：{cur_s}\\n"
            "请发送新的 Chat ID（纯数字，如 -100xxxxxxxxxx）。\\n"
            "发送后写入配置，再点「增量扫描导入」。\\n"
            "回复 取消 可中止。"
        )
        await callback.answer()

    @dp.message(F.text.regexp(r"^-?\\\\d{6,20}$") | F.text.in_({"取消", "cancel"}))
    async def forum_source_id_message(message: Message):
        uid = message.from_user.id
        if not is_admin(uid) or not _pending_source.get(uid):
            return
        raw = (message.text or "").strip()
        if raw in ("取消", "cancel"):
            _pending_source.pop(uid, None)
            await message.answer("已取消。")
            return
        new_id = int(raw)
        await set_source_chat_id(db_path, new_id)
        _pending_source.pop(uid, None)
        await message.answer(
            f"✅ 源仓库已设为 {new_id}\\n"
            "请打开「资源仓库扫描」→「增量扫描导入」或「全量重建导入」。\\n"
            "扫描完成后随机资源即可看到新分组。\\n"
            "请确认悦己 Bot 已加入该群并有读权限。"
        )
'''
        if "Message" not in text.split("from aiogram.types import")[1].split("\n")[0]:
            text = text.replace(
                "from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton",
                "from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message",
            )
        if text.rstrip().endswith("await forum_topics_page(callback)"):
            text = text.rstrip() + "\n" + handlers
        else:
            text = text.rstrip() + "\n" + handlers

    if "src_line" not in text:
        text = text.replace(
            "summary = await status_summary()\n        running = is_import_running()",
            "summary = await status_summary()\n        running = is_import_running()\n"
            "        try:\n"
            "            _sid = await get_source_chat_id(db_path)\n"
            "            src_line = f\"当前源仓库 ID：{_sid}\\n\"\n"
            "        except Exception:\n"
            "            src_line = \"当前源仓库：未设置（请先点更换源仓库 ID）\\n\"\n",
            1,
        )
        text = text.replace(
            'f"{\'🟡 进行中\' if running else \'🟢 空闲\'}\\n\\n"\n',
            'f"{\'🟡 进行中\' if running else \'🟢 空闲\'}\\n\\n"\n            f"{src_line}\\n"\n',
            1,
        )

    compile(text, "bot_forum_ui.py", "exec")
    path.write_text(text, encoding="utf-8")
    print("OK: bot_forum_ui.py → 后台可更换源仓库 ID")


def main() -> None:
    patch_forum_import_service()
    patch_bot_forum_ui()
    print(
        """
完成。请执行:
  python3 -m py_compile forum_import_service.py bot_forum_ui.py
  pm2 restart yueji

使用:
  机器人设置 → 资源仓库扫描 → 🔄 更换源仓库 ID → 发送新群 ID
  → 📥 增量扫描导入（或全量）
  → 用户侧随机资源即可看到新分组
"""
    )


if __name__ == "__main__":
    main()
