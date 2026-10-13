"""Import status helpers for Yueji admin UI."""
import aiosqlite

# DB_PATH is set by bot.py before calling these helpers.


async def get_import_status_summary(db_path: str) -> str:
    """Return human-readable last forum import status if available."""
    try:
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='import_status'"
            ) as cur:
                if not await cur.fetchone():
                    return "尚无导入记录（请先在服务器运行 import_forum.py --apply）"
            async with db.execute(
                """SELECT status, started_at, finished_at, topics_total, topics_imported,
                          topics_skipped, topics_failed, resources_total, resources_new,
                          resources_skipped, video_count, photo_count, album_groups,
                          file_count, last_error
                   FROM import_status WHERE id=1"""
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return "尚无导入记录"
        (status, started, finished, t_total, t_imp, t_skip, t_fail,
         r_total, r_new, r_skip, v, p, albums, files, err) = row
        lines = [
            f"状态：{status or 'unknown'}",
            f"开始：{started or '-'}",
            f"结束：{finished or '-'}",
            f"主题总数 / 成功 / 跳过 / 失败：{t_total or 0} / {t_imp or 0} / {t_skip or 0} / {t_fail or 0}",
            f"资源总计 / 新增 / 跳过：{r_total or 0} / {r_new or 0} / {r_skip or 0}",
            f"视频 {v or 0} · 照片 {p or 0} · 相册组 {albums or 0} · 其他 {files or 0}",
        ]
        if err:
            lines.append(f"最近错误：{str(err)[:200]}")
        return "\n".join(lines)
    except Exception as exc:
        return f"读取导入状态失败：{str(exc)[:150]}"
