async def send_source_messages(chat_id: int, rows, preview: bool = False, preview_limit: int = 3):
    """Copy source messages to user. preview=True sends up to preview_limit items (default 3).

    Error policy:
    - Permanent Telegram errors (message not found / deleted / no rights / chat not found):
      log once and skip that message; never infinite-retry.
    - Transient network/SSL/timeout/flood: limited retry with short backoff, then skip.
    - Batch copy_messages fails → fall back to per-message copy_message only once per id.
    """
    import logging
    import asyncio
    from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter, TelegramNetworkError

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

    async def _copy_one(source_chat_id: str, mid: int, max_attempts: int = 2) -> bool:
        nonlocal sent, skipped_permanent
        last_exc = None
        for attempt in range(1, max_attempts + 1):
            try:
                await bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=source_chat_id,
                    message_id=mid,
                    protect_content=True,
                )
                sent += 1
                return True
            except TelegramRetryAfter as e:
                wait = min(int(getattr(e, "retry_after", 3) or 3), 15)
                logger.warning(
                    "copy_message flood wait chat=%s mid=%s wait=%ss",
                    source_chat_id, mid, wait,
                )
                await asyncio.sleep(wait)
                last_exc = e
                continue
            except Exception as e2:
                last_exc = e2
                if _is_permanent(e2):
                    skipped_permanent += 1
                    logger.warning(
                        "copy_message permanent skip chat=%s mid=%s err=%s",
                        source_chat_id, mid, str(e2)[:180],
                    )
                    return False
                if _is_transient(e2) and attempt < max_attempts:
                    logger.warning(
                        "copy_message transient retry %s/%s chat=%s mid=%s err=%s",
                        attempt, max_attempts, source_chat_id, mid, str(e2)[:120],
                    )
                    await asyncio.sleep(0.6 * attempt)
                    continue
                logger.warning(
                    "copy_message failed chat=%s mid=%s err=%s",
                    source_chat_id, mid, str(e2)[:180],
                )
                return False
        if last_exc is not None:
            logger.warning(
                "copy_message exhausted retries chat=%s mid=%s err=%s",
                source_chat_id, mid, str(last_exc)[:180],
            )
        return False

    for source_chat_id, message_ids in by_chat.items():
        message_ids = sorted(set(message_ids))
        batch_ok = False
        if len(message_ids) > 1:
            try:
                await bot.copy_messages(
                    chat_id=chat_id,
                    from_chat_id=source_chat_id,
                    message_ids=message_ids,
                    protect_content=True,
                )
                sent += len(message_ids)
                batch_ok = True
            except Exception as exc:
                logger.info(
                    "copy_messages batch failed chat=%s n=%s err=%s → fallback single",
                    source_chat_id, len(message_ids), str(exc)[:160],
                )

        if not batch_ok:
            for mid in message_ids:
                await _copy_one(source_chat_id, mid)
                await asyncio.sleep(0.08)

    if preview and sent == 0:
        raise RuntimeError(
            "预览文件发送失败，请确认悦己机器人已加入源论坛/频道且有读取权限"
            + (f"（已跳过永久错误 {skipped_permanent} 条）" if skipped_permanent else "")
        )
    return sent


async def send_source_folder_card(message: Message, folder, user_id: int | None = None):
    """Random/recommend card: title + counts, then up to 3 preview media."""
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

    text = (
        f"📁 {name}\n"
        f"━━━━━━━━━━━━━━\n"
        f"📦 本分组共 {total} 个文件\n"
        f"🎬 视频/动画：{videos}\n"
        f"🖼️ 照片：{images}\n"
        f"📄 其他：{files}\n"
        f"🔓 解锁整个分组：{price} 积分\n"
        f"💎 你的余额：{points} 积分\n\n"
        f"下方为预览（最多 3 个），解锁后可浏览全部。"
    )

    fav_on = False
    try:
        from favorites_service import is_favorited, format_latest_updates_block, ensure_favorites_schema
        await ensure_favorites_schema(DB_PATH)
        fav_on = await is_favorited(DB_PATH, user_id, str(fid))
        latest_block = await format_latest_updates_block(DB_PATH, limit=5)
        if latest_block:
            text += "\n\n" + latest_block
    except Exception:
        fav_on = False

    fav_btn = InlineKeyboardButton(
        text="💔 取消收藏" if fav_on else "⭐ 收藏分组",
        callback_data=f"fav_toggle:{fid}",
    )
    if already_unlocked:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="▶️ 开始浏览资源", callback_data=f"source_browse_start:{fid}")],
            [fav_btn],
            [InlineKeyboardButton(text="🎲 再随机一个", callback_data="source_random_again")],
            [InlineKeyboardButton(text="⭐ 我的收藏", callback_data="fav_list:0"),
             InlineKeyboardButton(text="📌 最新更新", callback_data="latest_updates")],
        ])
        text += "\n\n✅ 你已解锁此分组。"
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🔓 解锁整个分组（{price} 积分）", callback_data=f"source_unlock:{fid}")],
            [fav_btn],
            [InlineKeyboardButton(text="🎲 再随机一个", callback_data="source_random_again")],
            [InlineKeyboardButton(text="⭐ 我的收藏", callback_data="fav_list:0"),
             InlineKeyboardButton(text="📌 最新更新", callback_data="latest_updates")],
        ])

    await message.answer(text, reply_markup=kb)

    preview_rows = []
    if candidates:
        videos_rows = [r for r in candidates if str(r[2] or "").lower() in ("video", "animation")]
        photo_rows = [r for r in candidates if str(r[2] or "").lower() in ("photo", "image", "picture")]
        other_rows = [r for r in candidates if r not in videos_rows and r not in photo_rows]
        picked = []
        for pool in (videos_rows, photo_rows, other_rows, candidates):
            for r in pool:
                key = (r[0], r[1])
                if key in {(x[0], x[1]) for x in picked}:
                    continue
                picked.append(r)
                if len(picked) >= 3:
                    break
            if len(picked) >= 3:
                break
        preview_rows = [(r[0], r[1]) for r in picked[:3]]

    if preview_rows:
        try:
            sent = await send_source_messages(
                message.chat.id, preview_rows, preview=True, preview_limit=3
            )
            if sent == 0:
                await message.answer("⚠️ 预览媒体暂时无法发送，请确认机器人已加入源论坛且有读取权限。")
        except Exception as exc:
            await message.answer("⚠️ 预览发送失败：" + str(exc)[:200])
    else:
        await message.answer("📭 此分组暂无已索引的媒体，请管理员重新扫描导入。")
    return True
