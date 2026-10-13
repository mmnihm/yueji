"""User favorites + admin mark-update UI. register_favorites_ui(dp, bot, ...)."""
from __future__ import annotations

import aiosqlite
from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message

from favorites_service import (
    ensure_favorites_schema,
    toggle_favorite,
    is_favorited,
    list_user_favorites,
    count_user_favorites,
    list_latest_updates,
    mark_folder_updated,
    notify_favoriters,
    get_folder_name,
    format_latest_updates_block,
)


def register_favorites_ui(
    dp,
    bot,
    *,
    db_path: str,
    is_admin,
    get_config=None,
):
    """Call once after dp is created. Safe alongside register_forum_ui."""

    @dp.callback_query(F.data.startswith("fav_toggle:"))
    async def fav_toggle(callback: CallbackQuery):
        folder_id = callback.data.split(":", 1)[1]
        user_id = callback.from_user.id
        await ensure_favorites_schema(db_path)
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                "SELECT 1 FROM source_folders WHERE source_folder_id=?",
                (str(folder_id),),
            ) as cur:
                if not await cur.fetchone():
                    await callback.answer("分组不存在", show_alert=True)
                    return
        now_on, msg = await toggle_favorite(db_path, user_id, folder_id)
        await callback.answer(msg, show_alert=False)
        try:
            if callback.message and callback.message.reply_markup:
                rows = []
                for row in callback.message.reply_markup.inline_keyboard:
                    new_row = []
                    for btn in row:
                        if btn.callback_data == f"fav_toggle:{folder_id}":
                            new_row.append(
                                InlineKeyboardButton(
                                    text="💔 取消收藏" if now_on else "⭐ 收藏分组",
                                    callback_data=f"fav_toggle:{folder_id}",
                                )
                            )
                        else:
                            new_row.append(btn)
                    rows.append(new_row)
                await callback.message.edit_reply_markup(
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
                )
        except Exception:
            pass

    @dp.callback_query(F.data.startswith("fav_list:"))
    async def fav_list(callback: CallbackQuery):
        try:
            page = max(0, int(callback.data.split(":")[1]))
        except ValueError:
            page = 0
        user_id = callback.from_user.id
        await ensure_favorites_schema(db_path)
        ps = 8
        total = await count_user_favorites(db_path, user_id)
        rows = await list_user_favorites(db_path, user_id, limit=ps, offset=page * ps)
        if not rows and page == 0:
            await callback.message.edit_text(
                "⭐ 我的收藏\n还没有收藏任何分组。\n在随机资源卡片上点「收藏分组」即可。",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="🎲 去随机", callback_data="source_random_again")],
                        [InlineKeyboardButton(text="📌 最新更新", callback_data="latest_updates")],
                    ]
                ),
            )
            await callback.answer()
            return
        buttons = []
        for fid, name, cnt, updated in rows:
            mark = "🆕" if updated else "📁"
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"{mark} {(name or fid)[:28]} ({cnt})",
                        callback_data=f"source_open:{fid}",
                    )
                ]
            )
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="💔 取消收藏",
                        callback_data=f"fav_toggle:{fid}",
                    )
                ]
            )
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"fav_list:{page-1}"))
        if (page + 1) * ps < total:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"fav_list:{page+1}"))
        if nav:
            buttons.append(nav)
        buttons.append(
            [
                InlineKeyboardButton(text="📌 最新更新", callback_data="latest_updates"),
                InlineKeyboardButton(text="↩️", callback_data="menu_home"),
            ]
        )
        await callback.message.edit_text(
            f"⭐ 我的收藏（{total}）\n第 {page+1} 页\n点标题打开分组。",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
        await callback.answer()

    @dp.callback_query(F.data == "latest_updates")
    async def latest_updates(callback: CallbackQuery):
        await ensure_favorites_schema(db_path)
        rows = await list_latest_updates(db_path, limit=12)
        if not rows:
            await callback.answer("暂无更新记录", show_alert=True)
            return
        lines = ["📌 最新更新\n━━━━━━━━━━━━━━"]
        buttons = []
        for fid, name, updated_at, note, cnt in rows:
            title = (name or fid)[:30]
            ts = (updated_at or "")[:16]
            lines.append(f"· {title}\n  {ts}" + (f" · {note[:30]}" if note else ""))
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"📁 {title}",
                        callback_data=f"source_open:{fid}",
                    )
                ]
            )
        buttons.append(
            [
                InlineKeyboardButton(text="⭐ 我的收藏", callback_data="fav_list:0"),
                InlineKeyboardButton(text="↩️", callback_data="menu_home"),
            ]
        )
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
        await callback.answer()

    @dp.callback_query(F.data == "admin_updates_menu")
    async def admin_updates_menu(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        await ensure_favorites_schema(db_path)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📋 选择分组标记更新", callback_data="admin_upd_page:0")],
                [InlineKeyboardButton(text="📌 查看最新更新", callback_data="latest_updates")],
                [InlineKeyboardButton(text="↩️ 返回", callback_data="settings_home")],
            ]
        )
        await callback.message.edit_text(
            "📢 分组更新提醒\n━━━━━━━━━━━━━━\n"
            "1. 把新内容转发到机器人有权限的中间群/频道\n"
            "2. 在此选择分组 →「标记更新并通知收藏用户」\n"
            "收藏了该分组的用户会收到带按钮的私聊提醒。",
            reply_markup=kb,
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("admin_upd_page:"))
    async def admin_upd_page(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        try:
            page = max(0, int(callback.data.split(":")[1]))
        except ValueError:
            page = 0
        ps, offset = 8, page * 8
        async with aiosqlite.connect(db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM source_folders") as cur:
                total = int((await cur.fetchone())[0] or 0)
            async with db.execute(
                """
                SELECT source_folder_id, name, item_count, updated_at
                FROM source_folders
                ORDER BY COALESCE(updated_at, '') DESC, source_folder_id DESC
                LIMIT ? OFFSET ?
                """,
                (ps, offset),
            ) as cur:
                rows = await cur.fetchall()
        if not rows:
            await callback.answer("暂无分组，请先导入", show_alert=True)
            return
        buttons = []
        for fid, name, cnt, updated in rows:
            label = f"{'🆕' if updated else '📁'} {(name or fid)[:26]} ({cnt})"
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=label,
                        callback_data=f"admin_mark_upd:{fid}",
                    )
                ]
            )
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"admin_upd_page:{page-1}"))
        if offset + ps < total:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"admin_upd_page:{page+1}"))
        if nav:
            buttons.append(nav)
        buttons.append([InlineKeyboardButton(text="↩️", callback_data="admin_updates_menu")])
        await callback.message.edit_text(
            f"选择要标记更新的分组\n第 {page+1} 页 / 共 {total}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("admin_mark_upd:"))
    async def admin_mark_upd(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        folder_id = callback.data.split(":", 1)[1]
        name = await get_folder_name(db_path, folder_id)
        n_fav = await mark_folder_updated(db_path, folder_id, note="管理员标记更新")
        await callback.answer("已标记更新", show_alert=False)
        await callback.message.answer(
            f"⏳ 正在通知收藏用户…\n分组：{name}\n收藏人数：{n_fav}"
        )
        ok, fail = await notify_favoriters(
            bot, db_path, folder_id, note="内容有更新，请打开查看"
        )
        await callback.message.answer(
            f"✅ 通知完成\n成功 {ok} · 失败 {fail}\n"
            f"（失败多为用户已停用 Bot 或未私聊启动）"
        )

    @dp.message(F.text.in_({"我的收藏", "⭐ 我的收藏"}))
    async def msg_my_favorites(message: Message):
        await ensure_favorites_schema(db_path)
        total = await count_user_favorites(db_path, message.from_user.id)
        block = await format_latest_updates_block(db_path, limit=5)
        text = f"⭐ 我的收藏：{total} 个分组\n点按钮查看列表。"
        if block:
            text += "\n\n" + block
        await message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⭐ 打开收藏列表", callback_data="fav_list:0")],
                    [InlineKeyboardButton(text="📌 最新更新", callback_data="latest_updates")],
                ]
            ),
        )
