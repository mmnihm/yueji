# 悦己分享机器人

合规 Telegram 资源分享机器人（Python + aiogram，SQLite）。

## 主要功能
- 用户积分、每日签到、积分档位、兑换码。
- 随机资源文件夹预览、积分解锁和分批发送。
- 管理员设置中心：初始积分、签到奖励、解锁价格、随机文件夹数量、每日新解锁文件夹上限。
- 解锁后浏览时长：3/5/10 分钟、自定义分钟或不限时；到期后可限制全部资源或只限制视频；可配置管理员是否免限时。
- 用户管理：按 Telegram ID/用户名搜索、增减积分、封禁/解封。
- 用户菜单名称可修改，菜单按钮可单独隐藏/显示并调整顺序；积分档位支持预设或自定义。
- 欢迎文案、帮助文案、公告、客服链接、欢迎图片可在后台管理。
- 管理员可将 SQLite 数据库备份发送到自己的 Telegram 私聊。
- 原资源仓库 MySQL 连接、频道绑定及资源索引同步功能保留；导入 Telegram 论坛后会自动阻止旧 MySQL 同步覆盖论坛索引。
- **Telegram 论坛自动索引导入工具 `import_forum.py`**：自动发现全部话题、安全过滤后批量导入；仅索引消息 ID，不下载媒体；支持增量补导与全量重建资源表；导入状态可在管理员「资源仓库管理」中查看。

## 安装与运行
1. 安装依赖：`pip install -r requirements.txt`
2. 复制 `.env.example` 为 `.env`，填写 `BOT_TOKEN` 和管理员数字 ID `ADMIN_ID`。
3. 运行：`python bot.py`

建议使用 screen 或 systemd 保持进程运行。

## 重要说明
- 浏览计时从用户点击「开始浏览资源」时启动。时间到后，机器人会阻止新的受限资源发送；已发送的 Telegram 消息无法撤回。
- 每日文件夹解锁上限设置为 0 表示不限量；已解锁文件夹不会重复扣积分。
- 购买积分当前只提供档位展示和兑换码流程，尚未接入真实在线支付。请仅向已完成付款的用户发放兑换码。
- 数据库备份包含用户 ID、积分、兑换码及解锁记录，备份文件应仅交给可信管理员并妥善保管。
- 本项目仅供合规内容使用，请勿用于违规资源。

## 自动检查
GitHub Actions 会执行 Python 语法检查和 SQLite 配置/迁移烟雾测试。部署前仍应在测试机器人中验证 Telegram 实际按钮、原仓库频道权限和资源分页表现。

## Telegram 论坛资源导入（全自动）

论坛 ID 默认 `-1002332481220`（可通过环境变量 `FORUM_CHAT_ID` 覆盖）。

### 前置条件
- 本机已有可用的 Telethon session（默认路径 `forum_reader_alt` 或环境变量 `FORUM_SESSION`）。
- 环境变量或 `.env` 中配置 `API_ID` / `API_HASH`（或 `TG_API_ID` / `TG_API_HASH`）。
- **不要**把 session、API Hash、Bot Token 提交到仓库。

### 推荐流程（VPS 上）
```bash
cd /root/yueji
# 1. 预览：列出全部主题并标记安全过滤结果（不写库）
python import_forum.py --list-topics

# 2. 干跑扫描（不写库）
python import_forum.py

# 3. 正式增量导入（只新增缺失资源，保留用户/积分/签到/兑换码）
python import_forum.py --apply

# 可选：全量重建资源索引（仍保留用户等业务数据）
python import_forum.py --apply --full-replace
```

### 安全过滤
主题标题或消息说明中命中未成年人、偷拍、非自愿泄露等关键词的内容会被自动跳过并记录原因。普通成人内容不会被这些规则排除。

### 增量与数据安全
- 默认 `--apply` 为增量：已存在的 `resource_key` 会跳过，不会重复插入。
- `--full-replace` 仅清空 `source_resources` / `source_folders` / 解锁与浏览会话，**绝不**删除 users、points、checkins、redemption_codes、config。
- 导入过程使用事务；失败主题会跳过并继续后续主题。
- 导入状态写入 `import_status` 表，管理员在「资源仓库管理」可查看成功/跳过/失败数量。

### 相册
资源表记录 `grouped_id`。机器人发送时通过 `copy_messages` 保持原相册分组，分页逻辑不会拆散同一相册。

### 权限
悦己机器人账号必须已加入目标论坛群，并具备读取消息权限，否则用户解锁后无法 `copy_messages`。
