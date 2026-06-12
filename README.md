# Telegram Group Backup & Download v1.1

Created by Poleroso.

A desktop tool for copying, organizing, downloading, and cleaning Telegram groups and forum topics.

## What it does

- Copy Telegram forum groups topic by topic into destination forum groups.
- Map source topics to existing destination topics or create new destination topics.
- Save projects and run them later from a queue.
- Download files, photos, videos, or text from selected topics into folders.
- Resume copy/download work between runs.
- Scan destination topics for files added outside the app.
- Repair copied messages that are missing hidden links.
- Find duplicate files already inside destination topics.
- Keep preview images linked to copied files instead of copying loose image spam.

## Privacy

This repository intentionally does not include personal runtime files:

- Telegram session files
- saved API credentials/config
- project state/progress JSON files
- downloaded media
- built executables

Those files are ignored by `.gitignore` and should stay local.

## Requirements

- Python 3.10 or newer
- A Telegram API ID and API hash from <https://my.telegram.org>

Install dependencies:

```bat
python -m pip install -r requirements.txt
```

Run the app:

```bat
python tg_backup_gui.py
```

## Build a Windows executable

Run:

```bat
build.bat
```

The executable will be created as:

```text
dist\TelegramGroupBackupDownload.exe
```

## Build a macOS app

See the `Mac` folder for macOS build files and instructions.

GitHub macOS builds produce:

```text
TelegramGroupBackupDownload-v1.1-macOS.zip
```

## Notes

This app uses Telethon and requires access to a Telegram account you control. Use it only with groups and content you are allowed to copy or download.
