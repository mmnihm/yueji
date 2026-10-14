# 悦己 Telegram 资源分享机器人

Python 3.10+、aiogram 3、SQLite。GitHub `main` 是代码唯一来源；Token、扫描号 session 和数据库不提交到仓库。

## 功能
- 用户菜单、个人信息、每日签到、积分余额、兑换码。
- 资源文件夹列表、关键词搜索、随机推荐、收藏。
- 积分解锁、每日解锁上限、浏览计时和到期范围设置。
- 资源卡片始终是独立文本消息并携带 InlineKeyboard，不依赖复制媒体后编辑 caption，因此按钮不会因相册复制限制而丢失。
- 分批复制源消息；按 grouped_id 保持相册完整，跨分页的相册会整体进入当前批次。
- 批量发送失败不自动整批重发，避免部分成功后重复发送。
- 管理员菜单：浏览设置、菜单文案/顺序（可隐藏菜单项）、公告、客服链接、统计、兑换码生成、数据库备份。
- 会员：支持管理员发放指定天数或永久会员；会员可免费解锁文件夹。管理员可调整用户积分并封禁/解封。
- 最新资源文件夹：按最新资源消息排序显示。
- 可选的 Telegram 论坛扫描导入管理界面（需要 Telethon session 和 API_ID/API_HASH）。

## 部署
1. 安装 Python 3.10+ 和 PM2。
2. 在服务器 `/root/yueji/.env` 配置 `BOT_TOKEN`、`ADMIN_ID`。可选配置 `YUEJI_DB`、`API_ID`、`API_HASH`、`FORUM_CHAT_ID`、`FORUM_SESSION`。
3. 安装依赖：`python3 -m pip install -r requirements.txt`
4. 检查：`python3 -m py_compile bot.py`；`python3 -m unittest discover -s tests -v`
5. PM2 启动命令示例：`pm2 start bot.py --name yueji --interpreter .venv/bin/python --cwd /root/yueji`

更新可执行 `bash deploy.sh`。部署脚本先备份数据库，再拉取 GitHub main、检查代码和测试，通过后重启 PM2。

## 数据安全
- `yueji.db`、`.env`、Telethon session 均由 `.gitignore` 排除。
- 部署脚本在更新前复制数据库备份到 `/root/yueji-backups/`。
- 不要在未备份的情况下删除数据库；论坛全量重建只应通过导入服务的明确选项执行。
- 兑换码流程不会进行在线收款；只有确认收款后才应把兑换码发给用户。

## 测试
GitHub Actions 执行语法检查和 SQLite 单元测试。Telegram 按钮、源频道权限及实际媒体复制仍需在测试机器人上验证。
