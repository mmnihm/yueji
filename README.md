# 悦己分享机器人

合规 Telegram 资源分享机器人基础框架（Python + aiogram）。

## 功能
- 底部键盘：我的信息 / 每日签到 / 购买积分 / 帮助
- 用户信息：用户名、当前积分、累计消费、已解锁数量
- 随机资源推送 + 积分解锁
- 每日签到
- 购买积分（多档位按钮）
- 帮助说明

## 安装与运行
1. 安装依赖：`pip install -r requirements.txt`
2. 复制 `.env.example` 为 `.env`，填写 `BOT_TOKEN`
3. 运行：`python bot.py`

建议用 screen 或 systemd 在服务器上保持运行。

## 注意
仅供合规内容使用，请勿用于违规资源。
