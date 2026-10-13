#!/usr/bin/env python3
"""Import Telegram forum topics into Yueji's SQLite resource index.

Only indexes message IDs; does not download media.
Default: import ALL topics except those matching hard safety filters
(minors / non-consensual). Optional allowlist/blocklist from env or DB config.

Hard safety filters cannot be disabled.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
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

# HARD safety filter — cannot be turned off.
HARD_BLOCK_TERMS = (
    "未成年", "萝莉", "少萝", "幼女", "童颜", "儿童", "小女孩", "未满",
    "小学生", "初中生", "高中生", "学生妹",
    "偷拍", "强奸", "迷奸", "下药", "黑料", "泄露", "流出", "外流",
    "非自愿", "报复性", "偷拍视频", "私密泄露",
)


def hard_blocked(text: str) -> bool:
    lowered = (text or "").casefold()
    return any(term.casefold() in lowered for term in HARD_BLOCK_TERMS)


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


def parse_id_set(raw: str) -> set:
    out = set()
    for part in (raw or "").replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            pass
    return out


async def load_prefs(db):
    mode = "all"
    allow = set()
    block = set()
    env_allow = os.getenv("FORUM_TOPIC_ALLOWLIST", "").strip()
    env_block = os.getenv("FORUM_TOPIC_BLOCKLIST", "").strip()
    if env_allow:
        mode = "allowlist"
        allow = parse_id_set(env_allow)
    if env_block:
        block = parse_id_set(env_block)
    try:
        async with db.execute(
            "SELECT key, value FROM config WHERE key IN (?, ?, ?)",
            ("forum_topic_mode", "forum_topic_allowlist", "forum_topic_blocklist"),
        ) as cur:
            rows = {k: v for k, v in await cur.fetchall()}
        if rows.get("forum_topic_mode") in ("all", "allowlist", "blocklist"):
            mode = rows["forum_topic_mode"]
        if rows.get("forum_topic_allowlist"):
            allow |= parse_id_set(rows["forum_topic_allowlist"])
            if mode == "all" and allow:
                mode = "allowlist"
        if rows.get("forum_topic_blocklist"):
            block |= parse_id_set(rows["forum_topic_blocklist"])
    except Exception:
        pass
    try:
        async with db.execute(
            "SELECT topic_id FROM forum_topic_prefs WHERE excluded=1"
        ) as cur:
            for (tid,) in await cur.fetchall():
                block.add(int(tid))
    except Exception:
        pass
    return mode, allow, block


async def ensure_schema(db):
    await db.execute("""
        CREATE TABLE IF NOT EXISTS import_status (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            started_at TEXT, finished_at TEXT, status TEXT,
            topics_total INTEGER DEFAULT 0, topics_imported INTEGER DEFAULT 0,
            topics_skipped INTEGER DEFAULT 0, topics_failed INTEGER DEFAULT 0,
            resources_total INTEGER DEFAULT 0, resources_new INTEGER DEFAULT 0,
            resources_skipped INTEGER DEFAULT 0, video_count INTEGER DEFAULT 0,
            photo_count INTEGER DEFAULT 0, album_groups INTEGER DEFAULT 0,
            file_count INTEGER DEFAULT 0, last_error TEXT, detail TEXT
        )
    """)
    await db.execute("INSERT OR IGNORE INTO import_status (id, status) VALUES (1, 'never')")
    await db.execute("""
        CREATE TABLE IF NOT EXISTS forum_topic_prefs (
            topic_id INTEGER PRIMARY KEY,
            title TEXT,
            excluded INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    async with db.execute("PRAGMA table_info(source_resources)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "grouped_id" not in cols:
        await db.execute("ALTER TABLE source_resources ADD COLUMN grouped_id TEXT")
    await db.commit()


async def write_status(db, **kwargs):
    cols = ", ".join(f"{k}=?" for k in kwargs)
    await db.execute(f"UPDATE import_status SET {cols} WHERE id=1", list(kwargs.values()))
    await db.commit()


async def get_topics(client, entity):
    topics, offset_topic, seen = [], 0, set()
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
        await asyncio.sleep(0.3)
    return topics


async def iter_topic_messages(client, entity, topic_id, max_retries=5):
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
            print(f"  主题 {topic_id} 限流，等待 {e.seconds}s")
            await asyncio.sleep(e.seconds + 1)
        except RPCError as e:
            attempt += 1
            if attempt > max_retries:
                raise
            await asyncio.sleep(2 * attempt)


def should_import(topic_id, title, mode, allow, block):
    if hard_blocked(title):
        return False, "硬安全过滤（未成年/非自愿等）"
    if topic_id in block:
        return False, "管理员排除列表"
    if mode == "allowlist" and topic_id not in allow:
        return False, "不在管理员允许列表"
    return True, ""


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-topics", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--full-replace", action="store_true")
    parser.add_argument("--limit-topics", type=int, default=0)
    parser.add_argument("--sync-prefs", action="store_true",
                        help="Refresh forum_topic_prefs titles only")
    args = parser.parse_args()

    env = {}
    for p in (ROOT / ".env", Path("/root/yueji/.env"), Path("/root/telegram-forward-bot/.env"), Path("/root/bot/.env")):
        try:
            env.update({k: v for k, v in dotenv_values(p).items() if v})
        except Exception:
            pass
    env.update({k: v for k, v in os.environ.items() if v})
    try:
        api_id = int(env.get("API_ID") or env.get("TELEGRAM_API_ID") or env.get("TG_API_ID") or "")
        api_hash = str(env.get("API_HASH") or env.get("TELEGRAM_API_HASH") or env.get("TG_API_HASH") or "")
    except (ValueError, TypeError):
        raise SystemExit("找不到 API_ID/API_HASH")
    if not api_hash:
        raise SystemExit("找不到 API_HASH")

    client = TelegramClient(SESSION_PATH, api_id, api_hash)
    await client.start()
    print(f"已连接 | forum={FORUM_CHAT_ID}")
    stats = defaultdict(int)
    stats["errors"] = []

    try:
        entity = await client.get_entity(FORUM_CHAT_ID)
        if not getattr(entity, "forum", False):
            raise SystemExit("目标不是论坛群")
        print(f"论坛: {getattr(entity, 'title', '')}")
        topics = await get_topics(client, entity)
        stats["topics_total"] = len(topics)
        print(f"主题总数: {len(topics)}")

        async with aiosqlite.connect(DB_PATH) as db:
            await ensure_schema(db)
            mode, allow, block = await load_prefs(db)
            print(f"模式: {mode} | 允许 {len(allow)} | 排除 {len(block)}")

            for t in topics:
                tid = int(t.id)
                ttitle = str(getattr(t, "title", "") or f"主题 {tid}")[:250]
                await db.execute(
                    """INSERT INTO forum_topic_prefs (topic_id, title, excluded, updated_at)
                       VALUES (?, ?, 0, datetime('now'))
                       ON CONFLICT(topic_id) DO UPDATE SET title=excluded.title, updated_at=datetime('now')""",
                    (tid, ttitle),
                )
            await db.commit()

            if args.list_topics or args.sync_prefs:
                for t in topics:
                    tid = int(t.id)
                    ttitle = str(getattr(t, "title", "") or f"主题 {tid}")
                    ok, reason = should_import(tid, ttitle, mode, allow, block)
                    flag = "【可导入】" if ok else f"【跳过:{reason}】"
                    print(f"{tid}\t{flag}\t{ttitle}")
                if not args.apply:
                    print("偏好已更新，未改资源索引。")
                    return

            if args.limit_topics > 0:
                topics = topics[: args.limit_topics]

            accepted = []
            for t in topics:
                tid = int(t.id)
                ttitle = str(getattr(t, "title", "") or f"主题 {tid}")
                ok, reason = should_import(tid, ttitle, mode, allow, block)
                if ok:
                    accepted.append(t)
                else:
                    stats["topics_skipped"] += 1
            print(f"待导入: {len(accepted)} (跳过 {stats['topics_skipped']})")

            if not args.apply:
                print("预览模式。加 --apply 写入。")
                return

            await write_status(db, started_at=datetime.now(timezone.utc).isoformat(),
                               finished_at=None, status="running",
                               topics_total=stats["topics_total"], topics_imported=0,
                               topics_skipped=stats["topics_skipped"], topics_failed=0,
                               resources_total=0, resources_new=0, resources_skipped=0,
                               video_count=0, photo_count=0, album_groups=0, file_count=0,
                               last_error=None,
                               detail=json.dumps({"mode": mode}, ensure_ascii=False))

            if args.full_replace:
                await db.execute("BEGIN IMMEDIATE")
                for tbl in ("source_resources", "source_folders", "source_folder_unlocks", "source_browse_sessions"):
                    await db.execute(f"DELETE FROM {tbl}")
                await db.commit()
                print("资源表已清空")

            existing_keys = set()
            if not args.full_replace:
                async with db.execute("SELECT resource_key FROM source_resources") as cur:
                    existing_keys = {r[0] for r in await cur.fetchall()}

            album_seen = set()
            for idx, topic in enumerate(accepted, 1):
                topic_id = int(topic.id)
                folder_id = f"forum_{topic_id}"
                ttitle = str(getattr(topic, "title", "") or f"主题 {topic_id}").strip()[:250]
                print(f"[{idx}/{len(accepted)}] {topic_id} | {ttitle}")
                folder_counts = {"all": 0, "image": 0, "video": 0, "file": 0}
                new_rows = []
                try:
                    async for msg in iter_topic_messages(client, entity, topic_id):
                        if not getattr(msg, "id", None):
                            continue
                        caption = str(getattr(msg, "message", "") or "")
                        if hard_blocked(caption):
                            continue
                        kind = classify(msg)
                        if not kind:
                            continue
                        msg_id = int(msg.id)
                        rkey = f"forum:{topic_id}:{msg_id}"
                        if rkey in existing_keys:
                            stats["resources_skipped"] += 1
                            folder_counts["all"] += 1
                            continue
                        grouped_id = str(msg.grouped_id) if getattr(msg, "grouped_id", None) else None
                        data = json.dumps({"provider": "telegram_forum", "topic_id": topic_id,
                                           "topic_title": ttitle, "grouped_id": grouped_id,
                                           "caption": caption[:1000]}, ensure_ascii=False)
                        new_rows.append((rkey, folder_id, str(FORUM_CHAT_ID), msg_id,
                                         (caption[:250] or ttitle), kind, "", data, grouped_id))
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
                        if grouped_id and (folder_id, grouped_id) not in album_seen:
                            album_seen.add((folder_id, grouped_id))
                            stats["album_groups"] += 1

                    await db.execute(
                        """INSERT INTO source_folders
                           (source_folder_id, name, item_count, image_count, video_count, file_count)
                           VALUES (?,?,?,?,?,?)
                           ON CONFLICT(source_folder_id) DO UPDATE SET
                           name=excluded.name, item_count=excluded.item_count,
                           image_count=excluded.image_count, video_count=excluded.video_count,
                           file_count=excluded.file_count""",
                        (folder_id, ttitle, folder_counts["all"], folder_counts["image"],
                         folder_counts["video"], folder_counts["file"]))
                    if new_rows:
                        await db.executemany(
                            """INSERT OR IGNORE INTO source_resources
                               (resource_key,folder_id,chat_id,message_id,title,file_type,file_id,data,grouped_id)
                               VALUES (?,?,?,?,?,?,?,?,?)""", new_rows)
                        stats["resources_new"] += len(new_rows)
                    stats["resources_total"] += folder_counts["all"]
                    stats["topics_imported"] += 1
                    await db.commit()
                    print(f"  -> 新 {len(new_rows)} | 媒体 {folder_counts['all']}")
                except Exception as exc:
                    stats["topics_failed"] += 1
                    stats["errors"].append(f"topic {topic_id}: {exc}")
                    print(f"  失败: {exc}")
                    await db.rollback()

            await db.execute("INSERT OR REPLACE INTO config(key,value) VALUES('source_provider','telegram_forum')")
            await db.execute("INSERT OR REPLACE INTO config(key,value) VALUES('source_repo_id',?)", (str(FORUM_CHAT_ID),))
            await write_status(db, finished_at=datetime.now(timezone.utc).isoformat(), status="completed",
                               topics_imported=stats["topics_imported"], topics_skipped=stats["topics_skipped"],
                               topics_failed=stats["topics_failed"], resources_total=stats["resources_total"],
                               resources_new=stats["resources_new"], resources_skipped=stats["resources_skipped"],
                               video_count=stats["video_count"], photo_count=stats["photo_count"],
                               album_groups=stats["album_groups"], file_count=stats["file_count"],
                               last_error="; ".join(stats["errors"][-3:]) if stats["errors"] else None)
            await db.commit()

        print("\n========== 完成 ==========")
        print(f"成功主题 {stats['topics_imported']} | 跳过 {stats['topics_skipped']} | 失败 {stats['topics_failed']}")
        print(f"新增资源 {stats['resources_new']} | 视频 {stats['video_count']} | 照片 {stats['photo_count']}")
        print("硬安全过滤始终生效。用户数据未删除。")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
