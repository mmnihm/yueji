# 收藏 / 最新更新 / 更新提醒 — 集成说明

## 功能
- 用户在随机分组卡片上「⭐ 收藏分组」（整组）
- 「我的收藏」「📌 最新更新」列表
- 管理员「标记更新并通知收藏用户」（带「打开分组」按钮）
- 配合：中间群/频道转发新消息 + Bot 有管理/读权限

## VPS 部署（只动 yueji，不动 bot 进程）

```bash
cd /root/yueji
cp -a bot.py "bot.py.bak.$(date +%Y%m%d%H%M%S)"
cp -a yueji.db "yueji.db.bak.$(date +%Y%m%d%H%M%S)"  # 可选

# 拉取本功能分支文件
git fetch origin feature/favorites-updates
git checkout origin/feature/favorites-updates -- \
  favorites_service.py favorites_ui.py random_preview_snippet.py INTEGRATION_FAVORITES.md

# 把预览/收藏按钮打进 bot.py
python3 patch_random_preview.py

# 在 bot.py 里注册 UI（若尚未注册）。找到 register_forum_ui 附近，增加：
#   from favorites_ui import register_favorites_ui
#   register_favorites_ui(dp, bot, db_path=DB_PATH, is_admin=is_admin)

# 若 bot.py 没有 source_open 回调，可临时映射到已有打开逻辑，例如：
#   @dp.callback_query(F.data.startswith("source_open:"))
#   async def source_open(cb): ...  # 与 source_browse_start / 展示卡片相同

python3 -m py_compile bot.py favorites_service.py favorites_ui.py
pm2 restart yueji
```

## 管理员入口
设置中心增加按钮 callback_data=`admin_updates_menu`，或私聊发文字后点设置里的「分组更新提醒」。

## 电报侧
1. 建私有频道/群，拉入悦己 Bot 并设管理员
2. 转发要分享的内容到该频道
3. 索引的 chat_id 改为该中间频道 ID（重新导入或改 source_resources）
4. 更新内容后：转发新消息 + 管理后台「标记更新并通知」

## 回滚
```bash
cp -a bot.py.bak.XXXX bot.py
# 可选删除 favorites_*.py
pm2 restart yueji
```
