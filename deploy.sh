#!/usr/bin/env bash
set -Eeuo pipefail
cd /root/yueji
test -f .env || { echo "缺少 /root/yueji/.env，先配置 BOT_TOKEN 和 ADMIN_ID"; exit 1; }
git fetch origin main
git checkout -f main 2>/dev/null || git checkout -B main origin/main
git reset --hard origin/main
git clean -fd
python3 -m venv .venv 2>/dev/null || true
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python -m py_compile bot.py bot_forum_ui.py forum_import_service.py import_forum.py
python -m unittest discover -s tests -v
if pm2 describe yueji >/dev/null 2>&1; then pm2 delete yueji; fi
pm2 start /root/yueji/bot.py --name yueji --interpreter /root/yueji/.venv/bin/python
pm2 save
pm2 status yueji
