async def send_source_messages(chat_id: int, rows, preview: bool = False, preview_limit: int = 3):
    """Copy source messages to user. preview=True sends up to preview_limit items (default 3)."""
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
    by_chat = {}
    for cid, mid in ordered:
        by_chat.setdefault(cid, []).append(mid)
    for source_chat_id, message_ids in by_chat.items():
        message_ids = sorted(set(message_ids))
        try:
            await bot.copy_messages(
                chat_id=chat_id,
                from_chat_id=source_chat_id,
                message_ids=message_ids,
                protect_content=True,
            )
            sent += len(message_ids)
        except Exception as exc:
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
                    print("Source copy one error:", str(e2)[:200])
            if preview and sent == 0:
                raise RuntimeError(
                    "预览文件发送失败，请确认悦己机器人已加入源论坛/频道且有读取权限："
                    + str(exc)[:250]
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
    if already_unlocked:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="▶️ 开始浏览资源", callback_data=f"source_browse_start:{fid}")],
            [InlineKeyboardButton(text="🎲 再随机一个", callback_data="source_random_again")],
        ])
        text += "\n\n✅ 你已解锁此分组。"
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🔓 解锁整个分组（{price} 积分）", callback_data=f"source_unlock:{fid}")],
            [InlineKeyboardButton(text="🎲 再随机一个", callback_data="source_random_again")],
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
