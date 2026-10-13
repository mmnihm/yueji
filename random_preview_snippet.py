async def send_source_messages(chat_id: int, rows, preview: bool = False, preview_limit: int = 3):
    """一次 batch 复制。"""
    import logging
    logger = logging.getLogger("yueji.copy")
    pairs = []
    for row in rows:
        if not row or not row[0] or not row[1]:
            continue
        pairs.append((str(row[0]), int(row[1])))
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
        except Exception:
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
    """美观卡片：原消息标题 + 作者备注；单文件标题与按钮同一条；多文件相册优先挂第一张。"""
    import json as _json

    fid, name, total, images, videos, files = folder
    user_id = int(user_id or message.from_user.id)
    points, _, _ = await get_or_create_user(user_id, message.from_user.username or "")
    price_value = await get_config("source_folder_price", "10")
    price = max(0, int(price_value if price_value is not None else 10))

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT chat_id, message_id, file_type, grouped_id, title, data
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

    def _orig_title(row) -> str:
        t = (row[4] or "").strip() if len(row) > 4 else ""
        return t[:200] if t else ""

    def _author_from_row(row) -> str:
        raw = row[5] if len(row) > 5 else None
        if raw:
            try:
                d = _json.loads(raw) if isinstance(raw, str) else (raw or {})
                for k in ("author", "author_name", "topic_title", "from", "channel"):
                    if d.get(k):
                        return str(d[k]).strip()[:80]
            except Exception:
                pass
        return (name or "").strip()[:80]

    album_buckets = {}
    for r in candidates:
        if r[3]:
            album_buckets.setdefault(str(r[3]), []).append(r)
    picked = None
    for _gid, rows in sorted(album_buckets.items(), key=lambda x: -len(x[1])):
        if len(rows) >= 2:
            picked = rows[:3]
            break
    if not picked and candidates:
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

    main_title = _orig_title(picked[0]) if picked else ""
    if not main_title:
        main_title = (name or "资源").strip()[:200]
    author = _author_from_row(picked[0]) if picked else (name or "")
    lines = [f"【{main_title}】"]
    if author and author not in main_title:
        lines.append(f"👤 作者：{author}")
    lines.append("━━━━━━━━━━━━━━")
    lines.append(f"📦 共 {total} 个文件 · 🎬{videos} 🖼️{images}")
    lines.append(f"🔓 {price} 积分 · 余额 {points}")
    if already_unlocked:
        lines.append("✅ 已解锁")
    card_caption = "\n".join(lines)[:1024]

    if already_unlocked:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="▶️ 开始浏览", callback_data=f"source_browse_start:{fid}")],
            [
                InlineKeyboardButton(text="⭐ 收藏", callback_data=f"fav_toggle:{fid}"),
                InlineKeyboardButton(text="🎲 再随机", callback_data="source_random_again"),
            ],
        ])
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🔓 解锁（{price}积分）", callback_data=f"source_unlock:{fid}")],
            [
                InlineKeyboardButton(text="⭐ 收藏", callback_data=f"fav_toggle:{fid}"),
                InlineKeyboardButton(text="🎲 再随机", callback_data="source_random_again"),
            ],
        ])

    if not picked:
        await message.answer(card_caption + "\n\n📭 暂无媒体", reply_markup=kb)
        return True

    source_chat_id = str(picked[0][0])
    mids = sorted({int(r[1]) for r in picked})

    if len(mids) == 1:
        try:
            await bot.copy_message(
                chat_id=message.chat.id,
                from_chat_id=source_chat_id,
                message_id=mids[0],
                caption=card_caption,
                reply_markup=kb,
                protect_content=True,
            )
            return True
        except Exception:
            try:
                await bot.copy_message(
                    chat_id=message.chat.id,
                    from_chat_id=source_chat_id,
                    message_id=mids[0],
                    protect_content=True,
                )
            except Exception:
                pass
            await message.answer(card_caption, reply_markup=kb)
            return True

    first_id = None
    try:
        result = await bot.copy_messages(
            chat_id=message.chat.id,
            from_chat_id=source_chat_id,
            message_ids=mids,
            protect_content=True,
        )
        if result:
            first = result[0]
            first_id = getattr(first, "message_id", None) or (
                int(first) if isinstance(first, int) else None
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

    if first_id is not None:
        try:
            await bot.edit_message_caption(
                chat_id=message.chat.id,
                message_id=int(first_id),
                caption=card_caption,
                reply_markup=kb,
            )
            return True
        except Exception:
            try:
                await bot.edit_message_caption(
                    chat_id=message.chat.id,
                    message_id=int(first_id),
                    caption=card_caption,
                )
            except Exception:
                pass

    await message.answer(card_caption, reply_markup=kb)
    return True
