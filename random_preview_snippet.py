async def send_source_messages(chat_id: int, rows, preview: bool = False, preview_limit: int = 3):
    """Copy source messages in one batch when possible."""
    import logging
    from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

    logger = logging.getLogger("yueji.copy")
    PERMANENT = (
        "message to copy not found", "message not found", "chat not found",
        "have no rights", "not enough rights", "channel_private", "peer_id_invalid",
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
    seen, ordered = set(), []
    for p in pairs:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    if preview:
        ordered = ordered[: max(1, min(10, int(preview_limit or 3)))]

    sent = 0
    by_chat = {}
    for cid, mid in ordered:
        by_chat.setdefault(cid, []).append(mid)

    for source_chat_id, message_ids in by_chat.items():
        message_ids = sorted(set(message_ids))
        try:
            if len(message_ids) == 1:
                await bot.copy_message(
                    chat_id=chat_id, from_chat_id=source_chat_id,
                    message_id=message_ids[0], protect_content=True,
                )
                sent += 1
            else:
                await bot.copy_messages(
                    chat_id=chat_id, from_chat_id=source_chat_id,
                    message_ids=message_ids, protect_content=True,
                )
                sent += len(message_ids)
        except Exception as exc:
            for mid in message_ids:
                try:
                    await bot.copy_message(
                        chat_id=chat_id, from_chat_id=source_chat_id,
                        message_id=mid, protect_content=True,
                    )
                    sent += 1
                except Exception as e2:
                    logger.warning("copy fail mid=%s %s", mid, str(e2)[:100])
    if preview and sent == 0:
        raise RuntimeError("预览发送失败，请确认机器人对源有读权限")
    return sent


async def send_source_folder_card(message: Message, folder, user_id: int | None = None):
    """先发最多3个媒体（一批），再强制发一条带完整按钮的文字卡片（按钮一定显示）。"""
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
        f"💎 余额：{points} 积分\n"
    )
    if already_unlocked:
        card_text += "\n✅ 你已解锁此分组\n请点下方按钮操作"
    else:
        card_text += "\n请点下方按钮解锁或收藏"

    rows_kb = []
    if already_unlocked:
        rows_kb.append([InlineKeyboardButton(
            text="▶️ 开始浏览资源", callback_data=f"source_browse_start:{fid}",
        )])
    else:
        rows_kb.append([InlineKeyboardButton(
            text=f"🔓 解锁整个分组（{price} 积分）", callback_data=f"source_unlock:{fid}",
        )])
    rows_kb.append([InlineKeyboardButton(
        text="⭐ 收藏分组", callback_data=f"fav_toggle:{fid}",
    )])
    rows_kb.append([InlineKeyboardButton(
        text="🎲 再随机一个", callback_data="source_random_again",
    )])
    rows_kb.append([
        InlineKeyboardButton(text="⭐ 我的收藏", callback_data="fav_list:0"),
        InlineKeyboardButton(text="📌 最新更新", callback_data="latest_updates"),
    ])
    kb = InlineKeyboardMarkup(inline_keyboard=rows_kb)

    if candidates:
        album_buckets = {}
        for r in candidates:
            if r[3]:
                album_buckets.setdefault(str(r[3]), []).append(r)
        picked = None
        for _gid, rows in sorted(album_buckets.items(), key=lambda x: -len(x[1])):
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
                    k = (str(r[0]), int(r[1]))
                    if k in {(str(x[0]), int(x[1])) for x in picked}:
                        continue
                    picked.append(r)
                    if len(picked) >= 3:
                        break
                if len(picked) >= 3:
                    break

        if picked:
            source_chat_id = str(picked[0][0])
            mids = sorted({int(r[1]) for r in picked})
            try:
                if len(mids) == 1:
                    await bot.copy_message(
                        chat_id=message.chat.id,
                        from_chat_id=source_chat_id,
                        message_id=mids[0],
                        protect_content=True,
                    )
                else:
                    await bot.copy_messages(
                        chat_id=message.chat.id,
                        from_chat_id=source_chat_id,
                        message_ids=mids,
                        protect_content=True,
                    )
            except Exception:
                for mid in mids:
                    try:
                        await bot.copy_message(
                            chat_id=message.chat.id,
                            from_chat_id=source_chat_id,
                            message_id=mid,
                            protect_content=True,
                        )
                    except Exception:
                        pass

    chat_id = message.chat.id
    try:
        await message.answer(card_text, reply_markup=kb)
    except Exception:
        try:
            await bot.send_message(chat_id, card_text, reply_markup=kb)
        except Exception as exc:
            try:
                await bot.send_message(chat_id, f"操作菜单\n{exc}"[:200])
            except Exception:
                pass

    if not candidates:
        try:
            await message.answer("📭 此分组暂无已索引的媒体。")
        except Exception:
            pass
    return True
