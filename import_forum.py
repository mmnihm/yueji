#!/usr/bin/env python3
"""CLI wrapper — prefer bot admin button 扫描导入论坛."""
from __future__ import annotations
import argparse
import asyncio
import sys
from forum_import_service import run_forum_import


async def _print(msg: str):
    print(msg)


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--list-topics", action="store_true")
    p.add_argument("--sync-prefs", action="store_true")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--full-replace", action="store_true")
    args = p.parse_args()
    if args.list_topics or args.sync_prefs:
        await run_forum_import(apply=False, sync_prefs_only=True, progress=_print)
        return
    if not args.apply:
        print("预览：加 --apply 写入。也可在机器人设置中心点「扫描导入论坛」。")
        await run_forum_import(apply=False, sync_prefs_only=True, progress=_print)
        return
    await run_forum_import(apply=True, full_replace=args.full_replace, progress=_print)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print("错误:", e)
        sys.exit(1)
