#!/usr/bin/env python3
"""Import Telegram forum topics into Yueji's SQLite resource index (fully automatic).

Only indexes message IDs; does not download or re-upload media.
Default mode is incremental (upsert, skip existing resource_keys).
Pass --full-replace to rebuild resource tables only (users/points/checkins/codes/config preserved).
Pass --dry-run (default without --apply) to scan and report without writing.
Safety: topics/messages matching BLOCK_TERMS (minors, non-consensual leak terms) are skipped.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import traceback
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from dotenv import dotenv_values
from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.functions.messages import GetForumTopicsRequest
from telethon.tl.types import DocumentAttributeAnimated, DocumentAttributeVideo

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("YUEJI_DB", str(ROOT / "yueji.db")))
SESSION_PATH = os.getenv("FORUM_SESSION", str(ROOT / "forum_reader_alt"))
FORUM_CHAT_ID = int(os.getenv("FORUM_CHAT_ID", "-1002332481220"))

# Safety filter: minors + non-consensual / leaked private content only.
# Ordinary adult content is NOT filtered by these terms.
BLOCK_TERMS = (
    "未成年", "萝莉", "少萝", "幼女", "童颜", "儿童", "小女孩", "未满",
    "小学生", "初中生", "高中生", "学生妹",
    "偷拍", "强奸", "迷奸", "下药", "黑料", "泄露", "流出", "外流",
    "非自愿", "报复性", "偷拍视频", "私密泄露",
)


def blocked(text: str) -> bool:
    lowered = (text or "").casefold()
    return any(term.casefold() in lowered for term in BLOCK_TERMS)


def classify(message) -> str:
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
    if getattr(message, "media", None):
        return "media"
    return ""


async def get_topics(client, entity):
    """Paginate all forum topics."""
    topics = []
    offset_topic = 0
    seen = set()
    while True:
        try:
            result = await client(GetForumTopicsRequest(
                peer=entity, q="", offset_date=0, offset_id=0,
                offset_topic=offset_topic, limit=100,
            ))
        except FloodWaitError as e:
            print(f"限流等待 {e.seconds}s ...")
            await asyncio.sleep(e.seconds + 1)
            continue
        page = list(getattr(result, "topics", []) or [])
        if not page:
            break
        fresh = [t for t in page if int(t.id) not in seen]
        topics.extend(fresh)
        seen.update(int(t.id) for t in fresh)
        if len(page) < 100 or not fresh:
            break
        offset_topic = int(page[-1].id)
        await asyncio.sleep(0.3)  # gentle pacing
    return topics


async def iter_topic_messages(client, entity, topic_id, max_retries=5):
    """Yield media messages in a topic with flood-wait handling."""
    attempt = 0
    while True:
        try:
            async for msg in client.iter_messages(entity, reply_to=topic_id, reverse=True):
                yield msg
            return
        except FloodWaitError as e:
            attempt += 1
            if attempt > max_retries:
                raise
            print(f"  主题 {topic_id} 限流，等待 {e.seconds}s (重试 {attempt}/{max_retries})")
            await asyncio.sleep(e.seconds + 1)
        except RPCError as e:
            attempt += 1
            if attempt > max_retries:
                raise
            print(f"  主题 {topic_id} RPC 错误: {e}，重试 {attempt}/{max_retries}")
            await asyncio.sleep(2 * attempt)


async def ensure_schema(db: aiosqlite.Connection):
    """Ensure import_status table and resource columns exist."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS import_status (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            started_at TEXT,
            finished_at TEXT,
            status TEXT,
            topics_total INTEGER DEFAULT 0,
            topics_imported INTEGER DEFAULT 0,
            topics_skipped INTEGER DEFAULT 0,
            topics_failed INTEGER DEFAULT 0,
            resources_total INTEGER DEFAULT 0,
            resources_new INTEGER DEFAULT 0,
            resources_skipped INTEGER DEFAULT 0,
            video_count INTEGER DEFAULT 0,
            photo_count INTEGER DEFAULT 0,
            album_groups INTEGER DEFAULT 0,
            file_count INTEGER DEFAULT 0,
            last_error TEXT,
            detail TEXT
        )
    """)
    await db.execute("""
        INSERT OR IGNORE INTO import_status (id, status) VALUES (1, 'never')
    """)
    # Ensure grouped_id column (already present in recent bot.py)
    async with db.execute("PRAGMA table_info(source_resources)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "grouped_id" not in cols:
        await db.execute("ALTER TABLE source_resources ADD COLUMN grouped_id TEXT")
    await db.commit()


async def write_status(db, **kwargs):
    cols = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values())
    await db.execute(f"UPDATE import_status SET {cols} WHERE id=1", vals)
    await db.commit()


async def main():
    parser = argparse.ArgumentParser(description="Yueji Telegram forum importer (auto)")
    parser.add_argument("--list-topics", action="store_true",
                        help="List all topic IDs/titles and exit (no DB write)")
    parser.add_argument("--apply", action="store_true",
                        help="Write to database (default is dry-run)")
    parser.add_argument("--full-replace", action="store_true",
                        help="Delete existing source_* tables then re-import (users/points kept)")
    parser.add_argument("--limit-topics", type=int, default=0,
                        help="Process at most N topics (0 = all, for testing)")
    args = parser.parse_args()

    # Load credentials without hardcoding
    env = {}
    for p in (ROOT / ".env", Path("/root/yueji/.env"), Path("/root/telegram-forward-bot/.env"),
              Path("/root/bot/.env")):
        try:
            env.update({k: v for k, v in dotenv_values(p).items() if v})
        except Exception:
            pass
    env.update({k: v for k, v in os.environ.items() if v})

    try:
        api_id = int(env.get("API_ID") or env.get("TELEGRAM_API_ID") or env.get("TG_API_ID") or "")
        api_hash = str(env.get("API_HASH") or env.get("TELEGRAM_API_HASH") or env.get("TG_API_HASH") or "")
    except (ValueError, TypeError):
        raise SystemExit("找不到有效的 API_ID/API_HASH，请在环境或 .env 中配置。")
    if not api_hash:
        raise SystemExit("找不到 API_HASH。")

    session = SESSION_PATH
    if not session.endswith(".session"):
        # Telethon accepts path without .session suffix
        pass

    client = TelegramClient(session, api_id, api_hash)
    await client.start()
    print(f"已连接 Telethon | session={session} | forum={FORUM_CHAT_ID}")

    stats = {
        "topics_total": 0,
        "topics_imported": 0,
        "topics_skipped": 0,
        "topics_failed": 0,
        "resources_total": 0,
        "resources_new": 0,
        "resources_skipped": 0,
        "video_count": 0,
        "photo_count": 0,
        "album_groups": 0,
        "file_count": 0,
        "errors": [],
    }

    try:
        entity = await client.get_entity(FORUM_CHAT_ID)
        title = getattr(entity, "title", "") or str(FORUM_CHAT_ID)
        if not getattr(entity, "forum", False):
            raise SystemExit(f"目标不是论坛群: {title}")
        print(f"论坛: {title}")

        topics = await get_topics(client, entity)
        stats["topics_total"] = len(topics)
        print(f"发现主题总数: {len(topics)}")

        if args.list_topics:
            for t in topics:
                ttitle = str(getattr(t, "title", "") or f"主题 {t.id}")
                flag = "【安全过滤跳过】" if blocked(ttitle) else "【可导入】"
                print(f"{int(t.id)}\t{flag}\t{ttitle}")
            print("列表完成，未修改数据库。")
            return

        if args.limit_topics > 0:
            topics = topics[: args.limit_topics]
            print(f"限制处理前 {len(topics)} 个主题（测试用）")

        # Pre-filter by title
        accepted = []
        for t in topics:
            ttitle = str(getattr(t, "title", "") or f"主题 {t.id}")
            if blocked(ttitle):
                stats["topics_skipped"] += 1
                stats["errors"].append(f"skip topic {t.id}: title blocked ({ttitle[:40]})")
            else:
                accepted.append(t)
        print(f"标题安全过滤后待处理主题: {len(accepted)} (跳过 {stats['topics_skipped']})")

        if not args.apply:
            print("【预览模式】不会写入数据库。加 --apply 才会真正导入。")
            # Still scan one topic for demo counts if any
            if accepted:
                sample = accepted[0]
                sid = int(sample.id)
                stitle = str(getattr(sample, "title", "") or sid)
                cnt = 0
                async for msg in iter_topic_messages(client, entity, sid):
                    if classify(msg):
                        cnt += 1
                    if cnt >= 20:
                        break
                print(f"示例主题 {sid} | {stitle} | 前 20 条媒体预估")
            print(f"预计可处理主题约 {len(accepted)} 个。使用 --apply 执行正式导入。")
            return

        # ---- APPLY MODE ----
        async with aiosqlite.connect(DB_PATH) as db:
            await ensure_schema(db)
            await write_status(
                db,
                started_at=datetime.now(timezone.utc).isoformat(),
                finished_at=None,
                status="running",
                topics_total=stats["topics_total"],
                topics_imported=0,
                topics_skipped=stats["topics_skipped"],
                topics_failed=0,
                resources_total=0,
                resources_new=0,
                resources_skipped=0,
                video_count=0,
                photo_count=0,
                album_groups=0,
                file_count=0,
                last_error=None,
                detail=json.dumps({"mode": "full-replace" if args.full_replace else "incremental"}, ensure_ascii=False),
            )

            if args.full_replace:
                print("执行 --full-replace：仅清空 source_* 资源相关表，保留用户/积分/签到/兑换码/配置")
                await db.execute("BEGIN IMMEDIATE")
                await db.execute("DELETE FROM source_resources")
                await db.execute("DELETE FROM source_folders")
                await db.execute("DELETE FROM source_folder_unlocks")
                await db.execute("DELETE FROM source_browse_sessions")
                await db.commit()
                print("资源表已清空")

            # Load existing keys for incremental skip
            existing_keys = set()
            if not args.full_replace:
                async with db.execute("SELECT resource_key FROM source_resources") as cur:
                    existing_keys = {row[0] for row in await cur.fetchall()}
                print(f"已有资源记录: {len(existing_keys)}，将跳过重复")

            album_seen = set()  # (folder_id, grouped_id) for counting unique albums

            for idx, topic in enumerate(accepted, 1):
                topic_id = int(topic.id)
                folder_id = f"forum_{topic_id}"
                ttitle = str(getattr(topic, "title", "") or f"主题 {topic_id}").strip()[:250]
                print(f"[{idx}/{len(accepted)}] 主题 {topic_id} | {ttitle}")

                folder_counts = {"all": 0, "image": 0, "video": 0, "file": 0}
                new_rows = []
                try:
                    async for msg in iter_topic_messages(client, entity, topic_id):
                        if not getattr(msg, "id", None):
                            continue
                        caption = str(getattr(msg, "message", "") or "")
                        if blocked(caption):
                            continue
                        kind = classify(msg)
                        if not kind:
                            continue
                        msg_id = int(msg.id)
                        rkey = f"forum:{topic_id}:{msg_id}"
                        if rkey in existing_keys:
                            stats["resources_skipped"] += 1
                            folder_counts["all"] += 1  # still count for folder stats refresh
                            if kind in ("photo", "image"):
                                folder_counts["image"] += 1
                            elif kind in ("video", "animation"):
                                folder_counts["video"] += 1
                            else:
                                folder_counts["file"] += 1
                            continue

                        grouped_id = str(msg.grouped_id) if getattr(msg, "grouped_id", None) else None
                        data = json.dumps({
                            "provider": "telegram_forum",
                            "topic_id": topic_id,
                            "topic_title": ttitle,
                            "grouped_id": grouped_id,
                            "caption": caption[:1000],
                        }, ensure_ascii=False)
                        new_rows.append((
                            rkey, folder_id, str(FORUM_CHAT_ID),
                            msg_id, (caption[:250] or ttitle), kind, "", data, grouped_id,
                        ))
                        existing_keys.add(rkey)
                        folder_counts["all"] += 1
                        if kind in ("photo", "image"):
                            folder_counts["image"] += 1
                            stats["photo_count"] += 1
                        elif kind in ("video", "animation"):
                            folder_counts["video"] += 1
                            stats["video_count"] += 1
                        else:
                            folder_counts["file"] += 1
                            stats["file_count"] += 1
                        if grouped_id:
                            album_key = (folder_id, grouped_id)
                            if album_key not in album_seen:
                                album_seen.add(album_key)
                                stats["album_groups"] += 1

                    # Upsert folder
                    await db.execute("""
                        INSERT INTO source_folders
                        (source_folder_id, name, item_count, image_count, video_count, file_count)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(source_folder_id) DO UPDATE SET
                            name=excluded.name,
                            item_count=excluded.item_count,
                            image_count=excluded.image_count,
                            video_count=excluded.video_count,
                            file_count=excluded.file_count
                    """, (folder_id, ttitle, folder_counts["all"],
                          folder_counts["image"], folder_counts["video"], folder_counts["file"]))

                    if new_rows:
                        await db.executemany("""
                            INSERT OR IGNORE INTO source_resources
                            (resource_key, folder_id, chat_id, message_id, title, file_type, file_id, data, grouped_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, new_rows)
                        stats["resources_new"] += len(new_rows)
                    stats["resources_total"] += folder_counts["all"]
                    stats["topics_imported"] += 1
                    await db.commit()
                    print(f"  -> 新资源 {len(new_rows)} | 累计媒体 {folder_counts['all']}")

                except Exception as exc:
                    stats["topics_failed"] += 1
                    err = f"topic {topic_id}: {type(exc).__name__}: {str(exc)[:200]}"
                    stats["errors"].append(err)
                    print(f"  失败: {err}")
                    await db.rollback()
                    # continue next topic

                # periodic status update
                if idx % 5 == 0 or idx == len(accepted):
                    await write_status(
                        db,
                        topics_imported=stats["topics_imported"],
                        topics_skipped=stats["topics_skipped"],
                        topics_failed=stats["topics_failed"],
                        resources_total=stats["resources_total"],
                        resources_new=stats["resources_new"],
                        resources_skipped=stats["resources_skipped"],
                        video_count=stats["video_count"],
                        photo_count=stats["photo_count"],
                        album_groups=stats["album_groups"],
                        file_count=stats["file_count"],
                        last_error=stats["errors"][-1] if stats["errors"] else None,
                    )

            # Final config flags
            await db.execute(
                "INSERT OR REPLACE INTO config(key,value) VALUES('source_provider','telegram_forum')"
            )
            await db.execute(
                "INSERT OR REPLACE INTO config(key,value) VALUES('source_repo_id',?)",
                (str(FORUM_CHAT_ID),),
            )
            await write_status(
                db,
                finished_at=datetime.now(timezone.utc).isoformat(),
                status="completed",
                topics_imported=stats["topics_imported"],
                topics_skipped=stats["topics_skipped"],
                topics_failed=stats["topics_failed"],
                resources_total=stats["resources_total"],
                resources_new=stats["resources_new"],
                resources_skipped=stats["resources_skipped"],
                video_count=stats["video_count"],
                photo_count=stats["photo_count"],
                album_groups=stats["album_groups"],
                file_count=stats["file_count"],
                last_error="; ".join(stats["errors"][-5:]) if stats["errors"] else None,
                detail=json.dumps({
                    "mode": "full-replace" if args.full_replace else "incremental",
                    "errors_sample": stats["errors"][:20],
                }, ensure_ascii=False),
            )
            await db.commit()

        print("\n========== 导入完成 ==========")
        print(f"主题总数: {stats['topics_total']}")
        print(f"成功导入主题: {stats['topics_imported']}")
        print(f"安全跳过主题: {stats['topics_skipped']}")
        print(f"失败主题: {stats['topics_failed']}")
        print(f"资源总计(含已有): {stats['resources_total']}")
        print(f"新增资源: {stats['resources_new']}")
        print(f"跳过已有资源: {stats['resources_skipped']}")
        print(f"视频/动画: {stats['video_count']}")
        print(f"照片: {stats['photo_count']}")
        print(f"相册组: {stats['album_groups']}")
        print(f"其他文件: {stats['file_count']}")
        if stats["errors"]:
            print(f"错误样本 (最多5条):")
            for e in stats["errors"][:5]:
                print(f"  - {e}")
        print("用户/积分/签到/兑换码/配置 均未删除。")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("用户中断")
        sys.exit(130)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
