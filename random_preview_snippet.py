async def send_source_messages(chat_id: int, rows, preview: bool = False, preview_limit: int = 3):
    """Copy source messages in one batch when possible (no per-file delay)."""
    import logging
    import asyncio
    from aiogram.exceptions import (
        TelegramBadRequest,
        TelegramForbiddenError,
        TelegramRetryAfter,
        TelegramNetworkError,
    )

    logger = logging.getLogger("yueji.copy")
    PERMANENT_MARKERS = (
        "message to copy not found",
        "message to forward not found",
        "message not found",
        "chat not found",
        "chat_id is empty",
        "bot is not a member",
        "have no rights",
        "not enough rights",
        "peer_id_invalid",
        "channel_invalid",
        "channel_private",
        "message_id_invalid",
    )

    def _is_permanent(exc: BaseException) -> bool:
        text = str(exc).lower()
        if isinstance(exc, (TelegramBadRequest, TelegramForbiddenError)):
            return any(m in text for m in PERMANENT_MARKERS) or True
        return any(m in text for m in PERMANENT_MARKERS)

    def _is_transient(exc: BaseException) -> bool:
        if isinstance(exc, (TelegramNetworkError, TelegramRetryAfter, asyncio.TimeoutError, OSError)):
            return True
        text = str(exc).lower()
        return any(
            k in text
            for k in (
                "ssl",
                "application_data_after_close_notify",
                "connection reset",
                "connection closed",
                "timeout",
                "temporarily unavailable",
                "server disconnected",
                "clientoserror",
            )
        )

    pairs = []
    for row in rows:
        if not row:
            continue
        source_chat_id, message_id = row[0], row[1]
        if not source_chat_id or not message_id:
            continue
        pairs.append((str(source_chat_id), int(message_id)))
    if not pairs:
        return 0

    seen = set()
    ordered = []
    for p in pairs:
        if p in seen:
            continue
        seen.add(p)
        ordered.append(p)
    if preview:
        ordered = ordered[: max(1, min(10, int(preview_limit or 3)))]

    sent = 0
    skipped_permanent = 0
    by_chat: dict = {}
    for cid, mid in ordered:
        by_chat.setdefault(cid, []).append(mid)

    for source_chat_id, message_ids in by_chat.items():
        message_ids = sorted(set(message_ids))
        try:
            if len(message_ids) == 1:
                await bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=source_chat_id,
                    message_id=message_ids[0],
                    protect_content=True,
                )
                sent += 1
            else:
                await bot.copy_messages(
                    chat_id=chat_id,
                    from_chat_id=source_chat_id,
                    message_ids=message_ids,
                    protect_content=True,
                )
                sent += len(message_ids)
        except Exception as exc:
            if _is_permanent(exc) and len(message_ids) == 1:
                skipped_permanent += 1
                logger.warning("copy permanent chat=%s mid=%s err=%s", source_chat_id, message_ids[0], str(exc)[:160])
                continue
            for mid in message_ids:
                try:
                    await bot.copy_message(
                        chat_id=chat_id,
                        from_chat_id=source_chat_id,
                        message_id=mid,
                        protect_content=True,
                    )
                    sent += 1
                except Exception as e2:
                    if _is_permanent(e2):
                        skipped_permanent += 1
                    logger.warning("copy one fail mid=%s err=%s", mid, str(e2)[:120])

    if preview and sent == 0:
        raise RuntimeError(
            "预览发送失败，请确认机器人已加入源频道且有读取权限"
            + (f"（跳过 {skipped_permanent}）" if skipped_permanent else "")
        )
    return sent


async def send_source_folder_card(message: Message, folder, user_id: int | None = None):
    """3 个文件尽量一次 copy_messages 成组；卡片文字写入第一张 caption；按钮保证显示。"""
    fid, name, total, images, videos, files = folder
    user_id = int(user_id or message.from_user.id)
    points, _, _ = await get_or_create_user(user_id, message.from_user.username or "")
    price_value = await get_config("source_folder_price", "10")
    price = max(0, int(price_value if price_value is not None else 10))

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT chat_id, message_id, file_type, grouped_id
               FROM source_resources WHERE folder_id=? ORDER BY message_id""",
            (str(fid),),
        ) as cur:
            candidates = await cur.fetchall()
        async with db.execute(
            "SELECT 1 FROM source_folder_unlocks WHERE user_id=? AND source_folder_id=?",
            (user_id, str(fid)),
        ) as cur:
            already_unlocked = await cur.fetchone()

    if candidates:
        total = len(candidates)
        images = sum(1 for r in candidates if str(r[2] or "").lower() in ("photo", "image", "picture"))
        videos = sum(1 for r in candidates if str(r[2] or "").lower() in ("video", "animation"))
        files = max(0, total - images - videos)

    card_text = (
        f"📁 {name}\n"
        f"━━━━━━━━━━━━━━\n"
        f"📦 本分组共 {total} 个文件\n"
        f"🎬 视频/动画：{videos}\n"
        f"🖼️ 照片：{images}\n"
        f"📄 其他：{files}\n"
        f"🔓 解锁：{price} 积分\n"
        f"💎 余额：{points} 积分"
    )
    if already_unlocked:
        card_text += "\n\n✅ 你已解锁此分组"
    else:
        card_text += "\n\n点击下方按钮解锁后可浏览全部"

    fav_on = False
    try:
        from favorites_service import is_favorited, format_latest_updates_block, ensure_favorites_schema
        await ensure_favorites_schema(DB_PATH)
        fav_on = await is_favorited(DB_PATH, user_id, str(fid))
        latest_block = await format_latest_updates_block(DB_PATH, limit=5)
        if latest_block:
            card_text += "\n\n" + latest_block
    except Exception:
        pass

    fav_btn = InlineKeyboardButton(
        text="💔 取消收藏" if fav_on else "⭐ 收藏分组",
        callback_data=f"fav_toggle:{fid}",
    )
    if already_unlocked:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="▶️ 开始浏览资源", callback_data=f"source_browse_start:{fid}")],
            [fav_btn],
            [InlineKeyboardButton(text="🎲 再随机一个", callback_data="source_random_again")],
            [
                InlineKeyboardButton(text="⭐ 我的收藏", callback_data="fav_list:0"),
                InlineKeyboardButton(text="📌 最新更新", callback_data="latest_updates"),
            ],
        ])
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🔓 解锁整个分组（{price} 积分）", callback_data=f"source_unlock:{fid}")],
            [fav_btn],
            [InlineKeyboardButton(text="🎲 再随机一个", callback_data="source_random_again")],
            [
                InlineKeyboardButton(text="⭐ 我的收藏", callback_data="fav_list:0"),
                InlineKeyboardButton(text="📌 最新更新", callback_data="latest_updates"),
            ],
        ])

    if not candidates:
        await message.answer(card_text, reply_markup=kb)
        await message.answer("📭 此分组暂无已索引的媒体，请管理员重新扫描导入。")
        return True

    album_buckets: dict = {}
    for r in candidates:
        gid = r[3]
        if gid:
            album_buckets.setdefault(str(gid), []).append(r)

    picked = None
    for gid, rows in sorted(album_buckets.items(), key=lambda x: -len(x[1])):
        if len(rows) >= 2:
            picked = rows[:3]
            break

    if not picked:
        photos = [r for r in candidates if str(r[2] or "").lower() in ("photo", "image", "picture")]
        vids = [r for r in candidates if str(r[2] or "").lower() in ("video", "animation")]
        others = [r for r in candidates if r not in photos and r not in vids]
        picked = []
        for pool in (photos, vids, others):
            for r in pool:
                key = (str(r[0]), int(r[1]))
                if key in {(str(x[0]), int(x[1])) for x in picked}:
                    continue
                picked.append(r)
                if len(picked) >= 3:
                    break
            if len(picked) >= 3:
                break

    source_chat_id = str(picked[0][0])
    mids = sorted(set(int(r[1]) for r in picked))
    caption = card_text[:1024]

    first_msg_id = None
    media_ok = False
    try:
        if len(mids) == 1:
            await bot.copy_message(
                chat_id=message.chat.id,
                from_chat_id=source_chat_id,
                message_id=mids[0],
                caption=caption,
                reply_markup=kb,
                protect_content=True,
            )
            return True
        else:
            result = await bot.copy_messages(
                chat_id=message.chat.id,
                from_chat_id=source_chat_id,
                message_ids=mids,
                protect_content=True,
            )
            media_ok = True
            if result:
                first = result[0]
                first_msg_id = getattr(first, "message_id", None) or (
                    int(first) if isinstance(first, int) else None
                )
    except Exception:
        try:
            await bot.copy_message(
                chat_id=message.chat.id,
                from_chat_id=source_chat_id,
                message_id=mids[0],
                caption=caption,
                reply_markup=kb,
                protect_content=True,
            )
            for mid in mids[1:]:
                try:
                    await bot.copy_message(
                        chat_id=message.chat.id,
                        from_chat_id=source_chat_id,
                        message_id=mid,
                        protect_content=True,
                    )
                except Exception:
                    pass
            return True
        except Exception as e2:
            await message.answer(caption, reply_markup=kb)
            await message.answer("⚠️ 预览失败：" + str(e2)[:150])
            return True

    if first_msg_id is not None:
        try:
            await bot.edit_message_caption(
                chat_id=message.chat.id,
                message_id=int(first_msg_id),
                caption=caption,
                reply_markup=kb,
            )
            return True
        except Exception:
            try:
                await bot.edit_message_caption(
                    chat_id=message.chat.id,
                    message_id=int(first_msg_id),
                    caption=caption,
                )
            except Exception:
                pass

    await message.answer(caption, reply_markup=kb)
    if not media_ok:
        await message.answer("⚠️ 预览媒体未能发送，请确认机器人对源有读权限。")
    return True
