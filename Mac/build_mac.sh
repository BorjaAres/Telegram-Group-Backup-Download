#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt pyinstaller

python3 -m PyInstaller \
  --windowed \
  --name "TelegramGroupBackupDownload" \
  --hidden-import tg_shared \
  --hidden-import tg_telegram \
  --hidden-import tg_workers \
  --hidden-import tg_ui \
  --add-data "Telegram Group Backup & Download v1.1 Guide.pdf:." \
  tg_backup_gui.py

echo "Mac app created at: dist/TelegramGroupBackupDownload.app"
