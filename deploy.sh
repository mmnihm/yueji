#!/usr/bin/env bash
set -Eeuo pipefail
cd /root/yueji
test -f .env || { echo "缺少 /root/yueji/.env，先配置 BOT_TOKEN 和 ADMIN_ID"; exit 1; }
mkdir -p /root/yueji-backups
if [ -f yueji.db ]; then
  cp -a yueji.db "/root/yueji-backups/yueji.db.$(date +%Y%m%d-%H%M%S).bak"
fi
git fetch origin main
git checkout main
git pull --ff-only origin main
python3 -m venv .venv 2>/dev/null || true
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python -m py_compile bot.py bot_forum_ui.py forum_import_service.py import_forum.py import_status_helper.py
python -m unittest discover -s tests -v
pm2 restart yueji --update-env
pm2 status yueji
