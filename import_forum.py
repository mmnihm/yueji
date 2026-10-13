#!/usr/bin/env python3
"""Import reviewed Telegram forum topics into Yueji's SQLite resource index.

This script only indexes message IDs; it does not download or re-upload media.
By default it performs a dry run. Pass --apply to replace the old resource index.
Set FORUM_TOPIC_ALLOWLIST to a comma-separated list of manually reviewed topic IDs.
Topics with minor-coded or likely non-consensual/leaked-content terms are always excluded.
"""
import argparse
import asyncio
import json
import os
import re
from collections import defaultdict
from pathlib import Path

import aiosqlite
from dotenv import dotenv_values
from telethon import TelegramClient
from telethon.tl.functions.channels import GetForumTopicsRequest
from telethon.tl.types import DocumentAttributeAnimated, DocumentAttributeVideo

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "yueji.db"
SESSION_PATH = os.getenv("FORUM_SESSION", str(ROOT / "forum_reader_alt"))
FORUM_CHAT_ID = int(os.getenv("FORUM_CHAT_ID", "-1002332481220"))
# Require explicit review rather than importing every topic from an unreviewed forum.
ALLOWLIST_RAW = os.getenv("FORUM_TOPIC_ALLOWLIST", "").strip()
BLOCK_TERMS = (
    "未成年", "萝莉", "少萝", "少女", "学生妹", "学生", "初中", "小学生",
    "高中生", "幼女", "童颜", "女儿", "妹妹", "儿童", "小女孩", "未满",
    "偷拍", "强奸", "迷奸", "下药", "黑料", "泄露", "流出", "外流",
    "非自愿", "报复性", "偷拍视频", "私密泄露",
)
MEDIA_TYPES = ("photo", "video", "animation", "document", "audio", "voice", "sticker")


def blocked(text: str) -> bool:
    lowered = (text or "").casefold()
    return any(term.casefold() in lowered for term in BLOCK_TERMS)


def classify(message) -> str:
    media = getattr(message, "media", None)
    if getattr(message, "photo", None):
        return "photo"
    document = getattr(message, "document", None)
    if document:
        attrs = getattr(document, "attributes", []) or []
        if any(isinstance(a, DocumentAttributeAnimated) for a in attrs):
            return "animation"
        if any(isinstance(a, DocumentAttributeVideo) for a in attrs):
            return "video"
        mime = str(getattr(document, "mime_type", "") or "").lower()
        if mime.startswith("video/"):
            return "video"
        if mime.startswith("image/"):
            return "image"
        if mime.startswith("audio/"):
            return "audio"
        return "document"
    if media:
        return "media"
    return ""


async def get_topics(client, entity):
    topics = []
    offset_topic = 0
    seen = set()
    while True:
        result = await client(GetForumTopicsRequest(
            channel=entity, q="", offset_date=None, offset_id=0,
            offset_topic=offset_topic, limit=100,
        ))
        page = list(getattr(result, "topics", []) or [])
        if not page:
            break
        fresh = [t for t in page if int(t.id) not in seen]
        topics.extend(fresh)
        seen.update(int(t.id) for t in fresh)
        if len(page) < 100 or not fresh:
            break
        offset_topic = int(page[-1].id)
    return topics


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-topics", action="store_true", help="list forum topic IDs and titles without changing the database")
    parser.add_argument("--apply", action="store_true", help="replace resource index after scanning")
    args = parser.parse_args()
    if not ALLOWLIST_RAW and not args.list_topics:
        raise SystemExit(
            "安全保护：尚未设置 FORUM_TOPIC_ALLOWLIST。先运行 --list-topics 审查主题，"
            "再只填写确认合规的主题 ID；本程序不会默认导入全部主题。"
        )
    try:
        allowlist = {int(x.strip()) for x in ALLOWLIST_RAW.split(",") if x.strip()}
    except ValueError:
        raise SystemExit("FORUM_TOPIC_ALLOWLIST 必须是逗号分隔的数字 topic ID。")

    env = {}
    env.update({k: v for k, v in dotenv_values(ROOT / ".env").items() if v})
    env.update({k: v for k, v in dotenv_values("/root/telegram-forward-bot/.env").items() if v})
    env.update({k: v for k, v in os.environ.items() if v})
    try:
        api_id = int(env.get("API_ID") or env.get("TELEGRAM_API_ID") or "")
        api_hash = str(env.get("API_HASH") or env.get("TELEGRAM_API_HASH") or "")
    except ValueError:
        raise SystemExit("找不到有效的 API_ID/API_HASH，请在本机环境配置；不要把密钥发给他人。")
    if not api_hash:
        raise SystemExit("找不到 API_HASH，请在本机环境配置；不要把密钥发给他人。")

    client = TelegramClient(SESSION_PATH, api_id, api_hash)
    await client.start()
    try:
        entity = await client.get_entity(FORUM_CHAT_ID)
        if not getattr(entity, "forum", False):
            raise SystemExit("指定频道不是 Telegram 论坛，请检查 FORUM_CHAT_ID。")
        topics = await get_topics(client, entity)
        print(f"论坛：{getattr(entity, 'title', '')} | 主题总数：{len(topics)}")
        if args.list_topics:
            for topic in topics:
                title = str(getattr(topic, "title", "") or f"主题 {topic.id}")
                status = "【需排除】" if blocked(title) else "【需人工审查】"
                print(f"{int(topic.id)}\t{status}\t{title}")
            print("只将人工确认合规的主题 ID 放入 FORUM_TOPIC_ALLOWLIST；本次未修改数据库。")
            return
        chosen = [t for t in topics if int(t.id) in allowlist]
        unknown = sorted(allowlist - {int(t.id) for t in topics})
        print(f"允许列表命中：{len(chosen)}")
        if unknown:
            print("警告：以下 ID 未找到，将忽略：" + ",".join(map(str, unknown)))

        accepted = []
        excluded = []
        for topic in chosen:
            title = str(getattr(topic, "title", "") or f"主题 {topic.id}")
            if blocked(title):
                excluded.append((int(topic.id), title, "主题名称触发安全过滤"))
            else:
                accepted.append(topic)
        if not accepted:
            raise SystemExit("没有通过主题名称安全过滤的已选主题；数据库未更改。")

        resource_rows = []
        folder_rows = []
        counts = defaultdict(lambda: {"all": 0, "image": 0, "video": 0, "file": 0})
        for topic in accepted:
            topic_id = int(topic.id)
            folder_id = f"forum_{topic_id}"
            title = str(getattr(topic, "title", "") or f"主题 {topic_id}").strip()[:250]
            folder_rows.append((folder_id, title))
            messages = []
            async for msg in client.iter_messages(entity, reply_to=topic_id, reverse=True):
                if not getattr(msg, "id", None):
                    continue
                caption = str(getattr(msg, "message", "") or "")
                if blocked(caption):
                    continue
                kind = classify(msg)
                if not kind:
                    continue
                grouped_id = str(msg.grouped_id) if getattr(msg, "grouped_id", None) else None
                data = json.dumps({
                    "provider": "telegram_forum",
                    "topic_id": topic_id,
                    "topic_title": title,
                    "grouped_id": grouped_id,
                    "caption": caption[:1000],
                }, ensure_ascii=False)
                resource_rows.append((
                    f"forum:{topic_id}:{int(msg.id)}", folder_id, str(FORUM_CHAT_ID),
                    int(msg.id), caption[:250] or title, kind, "", data, grouped_id,
                ))
                counts[folder_id]["all"] += 1
                if kind in ("photo", "image"):
                    counts[folder_id]["image"] += 1
                elif kind in ("video", "animation"):
                    counts[folder_id]["video"] += 1
                else:
                    counts[folder_id]["file"] += 1
                messages.append(msg.id)
            print(f"主题 {topic_id} | {title} | 媒体消息 {len(messages)}")
        print(f"扫描完成：{len(folder_rows)} 个主题，{len(resource_rows)} 条媒体消息，过滤主题 {len(excluded)} 个。")
        for topic_id, title, reason in excluded:
            print(f"已排除 {topic_id}: {title}（{reason}）")
        if not args.apply:
            print("当前为预览模式，数据库未更改。确认主题范围后，设置 allowlist 并添加 --apply 才会替换旧资源索引。")
            return

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            # Replace only resource index and stale resource browsing state.
            # Users, points, check-ins, redemption codes and config are preserved.
            await db.execute("DELETE FROM source_resources")
            await db.execute("DELETE FROM source_folders")
            await db.execute("DELETE FROM source_folder_unlocks")
            await db.execute("DELETE FROM source_browse_sessions")
            for folder_id, title in folder_rows:
                st = counts[folder_id]
                await db.execute(
                    """INSERT INTO source_folders
                    (source_folder_id,name,item_count,image_count,video_count,file_count)
                    VALUES (?,?,?,?,?,?)""",
                    (folder_id, title, st["all"], st["image"], st["video"], st["file"]),
                )
            await db.executemany(
                """INSERT INTO source_resources
                (resource_key,folder_id,chat_id,message_id,title,file_type,file_id,data,grouped_id)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                resource_rows,
            )
            await db.execute(
                "INSERT OR REPLACE INTO config(key,value) VALUES('source_provider','telegram_forum')"
            )
            await db.execute(
                "INSERT OR REPLACE INTO config(key,value) VALUES('source_repo_id',?)",
                (str(FORUM_CHAT_ID),),
            )
            await db.commit()
        print(f"导入完成：目录 {len(folder_rows)} 个，资源 {len(resource_rows)} 条。用户及积分数据未删除。")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
