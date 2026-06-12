# Telegram Group Backup & Download - Code Layout

The app is split by responsibility:

- `tg_backup_gui.py`  
  Main Tkinter app, tabs, buttons, queue UI, and log display.

- `tg_workers.py`  
  Long-running Telegram jobs: copy projects, legacy copy modes, downloads, duplicate scanning, duplicate deletion, and missing-link repair.

- `tg_telegram.py`  
  Login, sign-in, group loading, topic loading, topic-name resolving, group creation, and topic creation.

- `tg_shared.py`  
  Config save/load, retry handling, media/file helpers, duplicate keys, link extraction, safe names, and shared formatting.

- `tg_ui.py`  
  App title/version, colors, fonts, and small UI layout helpers.

The self-contained `Mac` folder mirrors the same source modules so it can be shared separately.

Do not put personal runtime data in the repo or Mac folder:

- Telegram session files
- `tg_backup_config.json`
- `state_*.json`
- downloaded media
- built executables/apps unless intentionally publishing a release
