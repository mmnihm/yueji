#!/usr/bin/env bash
set -Eeuo pipefail
cd /root/yueji
test -f .env || { echo "缺少 /root/yueji/.env，先配置 BOT_TOKEN 和 ADMIN_ID"; exit 1; }
DB_FILE="${YUEJI_DB:-/root/yueji/yueji.db}"
case "$DB_FILE" in /*) ;; *) DB_FILE="/root/yueji/$DB_FILE" ;; esac
mkdir -p /root/yueji-backups
if [ -f "$DB_FILE" ]; then
  python3 -c 'import os,sqlite3,sys; p=sys.argv[1]; c=sqlite3.connect(p); c.execute("PRAGMA wal_checkpoint(FULL)"); c.close()' "$DB_FILE" || true
  cp -a "$DB_FILE" "/root/yueji-backups/yueji.db.$(date +%Y%m%d-%H%M%S).bak"
fi
git fetch origin main
git checkout -f main 2>/dev/null || git checkout -B main origin/main
git reset --hard origin/main
git clean -fd
python3 -m venv .venv 2>/dev/null || true
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python -m py_compile bot.py bot_forum_ui.py forum_import_service.py import_forum.py import_status_helper.py
python -m unittest discover -s tests -v
if pm2 describe yueji >/dev/null 2>&1; then pm2 delete yueji; fi
pm2 start /root/yueji/bot.py --name yueji --interpreter /root/yueji/.venv/bin/python
pm2 save
pm2 status yueji
