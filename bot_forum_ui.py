"""Admin UI: 扫描导入论坛 — register with register_forum_ui(dp)."""
from __future__ import annotations

import asyncio
import aiosqlite
from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

try:
    from forum_import_service import run_forum_import, is_import_running
except ImportError:
    run_forum_import = None
    def is_import_running():
        return False


def register_forum_ui(dp, bot, *, db_path: str, is_admin, get_config, set_config):
    """Call once after dp is created."""

    async def ensure_prefs():
        async with aiosqlite.connect(db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS forum_topic_prefs (
                    topic_id INTEGER PRIMARY KEY, title TEXT,
                    excluded INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT DEFAULT (datetime('now'))
                )
            """)
            await db.commit()

    async def status_summary() -> str:
        try:
            async with aiosqlite.connect(db_path) as db:
                async with db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='import_status'"
                ) as cur:
                    if not await cur.fetchone():
                        return "尚无导入记录"
                async with db.execute(
                    """SELECT status, topics_total, topics_imported, topics_skipped, topics_failed,
                              resources_new, video_count, photo_count FROM import_status WHERE id=1"""
                ) as cur:
                    row = await cur.fetchone()
            if not row:
                return "尚无导入记录"
            st, tt, ti, ts, tf, rn, v, p = row
            return f"状态:{st} 主题{tt}/{ti}/跳过{ts}/失败{tf} 新增{rn} 视频{v} 照片{p}"
        except Exception as e:
            return str(e)[:100]

    @dp.callback_query(F.data == "forum_scan_menu")
    async def forum_scan_menu(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        summary = await status_summary()
        running = is_import_running()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔍 仅刷新话题列表", callback_data="forum_do:sync")],
            [InlineKeyboardButton(text="📥 增量扫描导入", callback_data="forum_do:import")],
            [InlineKeyboardButton(text="♻️ 全量重建导入", callback_data="forum_do:full")],
            [InlineKeyboardButton(text="🗂️ 话题选择", callback_data="forum_topics_manage")],
            [InlineKeyboardButton(text="↩️ 返回", callback_data="settings_home")],
        ])
        await callback.message.edit_text(
            "📡 论坛扫描导入\n━━━━━━━━━━━━━━\n"
            f"{'🟡 进行中' if running else '🟢 空闲'}\n\n"
            "点按钮后，机器人用扫描号自动读论坛并写入，无需 SSH 敲命令。\n\n"
            f"上次：{summary}",
            reply_markup=kb,
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("forum_do:"))
    async def forum_do(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        if run_forum_import is None:
            await callback.answer("缺少 forum_import_service.py", show_alert=True)
            return
        if is_import_running():
            await callback.answer("已有任务在运行", show_alert=True)
            return
        action = callback.data.split(":", 1)[1]
        admin_id = callback.from_user.id
        await callback.answer("已开始")
        await callback.message.answer("⏳ 扫描已启动，进度会发到这里…")

        async def progress(msg: str):
            try:
                await bot.send_message(admin_id, msg)
            except Exception:
                pass

        async def job():
            try:
                if action == "sync":
                    await run_forum_import(apply=False, sync_prefs_only=True, progress=progress, db_path=db_path)
                elif action == "full":
                    await run_forum_import(apply=True, full_replace=True, progress=progress, db_path=db_path)
                else:
                    await run_forum_import(apply=True, full_replace=False, progress=progress, db_path=db_path)
            except Exception as exc:
                try:
                    await bot.send_message(admin_id, f"❌ {str(exc)[:500]}")
                except Exception:
                    pass

        asyncio.create_task(job())

    @dp.callback_query(F.data == "forum_topics_manage")
    async def forum_topics_manage(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        await ensure_prefs()
        async with aiosqlite.connect(db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM forum_topic_prefs") as cur:
                total = int((await cur.fetchone())[0] or 0)
            async with db.execute("SELECT COUNT(*) FROM forum_topic_prefs WHERE excluded=1") as cur:
                excluded = int((await cur.fetchone())[0] or 0)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 浏览排除/恢复", callback_data="forum_topics_page:0")],
            [InlineKeyboardButton(text="📡 扫描导入", callback_data="forum_scan_menu")],
            [InlineKeyboardButton(text="↩️ 返回", callback_data="settings_home")],
        ])
        await callback.message.edit_text(
            f"🗂️ 话题选择\n缓存 {total} · 已排除 {excluded}\n"
            "硬安全过滤无法关闭。先刷新列表再排除，再增量导入。",
            reply_markup=kb,
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("forum_topics_page:"))
    async def forum_topics_page(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        await ensure_prefs()
        try:
            page = max(0, int(callback.data.split(":")[1]))
        except ValueError:
            page = 0
        ps, offset = 8, page * 8
        async with aiosqlite.connect(db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM forum_topic_prefs") as cur:
                total = int((await cur.fetchone())[0] or 0)
            async with db.execute(
                "SELECT topic_id, title, excluded FROM forum_topic_prefs ORDER BY excluded DESC, topic_id DESC LIMIT ? OFFSET ?",
                (ps, offset),
            ) as cur:
                rows = await cur.fetchall()
        if not rows:
            await callback.answer("请先「仅刷新话题列表」", show_alert=True)
            return
        buttons = []
        for tid, title, excluded in rows:
            mark = "🚫" if excluded else "✅"
            buttons.append([InlineKeyboardButton(
                text=f"{mark} {tid} {(title or '')[:28]}",
                callback_data=f"forum_toggle:{tid}:{page}",
            )])
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"forum_topics_page:{page-1}"))
        if offset + ps < total:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"forum_topics_page:{page+1}"))
        if nav:
            buttons.append(nav)
        buttons.append([InlineKeyboardButton(text="↩️", callback_data="forum_topics_manage")])
        await callback.message.edit_text(
            f"话题 第{page+1}页/共{total}\n点切换导入或排除",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("forum_toggle:"))
    async def forum_toggle(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        parts = callback.data.split(":")
        tid, page = int(parts[1]), int(parts[2]) if len(parts) > 2 else 0
        async with aiosqlite.connect(db_path) as db:
            async with db.execute("SELECT excluded FROM forum_topic_prefs WHERE topic_id=?", (tid,)) as cur:
                row = await cur.fetchone()
            if not row:
                await callback.answer("请先刷新列表", show_alert=True)
                return
            new_val = 0 if int(row[0]) else 1
            await db.execute(
                "UPDATE forum_topic_prefs SET excluded=?, updated_at=datetime('now') WHERE topic_id=?",
                (new_val, tid),
            )
            await db.commit()
        await callback.answer("已排除" if new_val else "已恢复")
        callback.data = f"forum_topics_page:{page}"
        await forum_topics_page(callback)
