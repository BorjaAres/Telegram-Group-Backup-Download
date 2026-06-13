# Telegram Group Backup & Download v2.1 for macOS

This folder is self-contained. It includes the app code and the Mac build script.

Included files:

- `tg_backup_gui.py`
- `tg_shared.py`
- `tg_telegram.py`
- `tg_workers.py`
- `tg_ui.py`
- `requirements.txt`
- `Telegram Group Backup & Download v2.1 Guide.pdf`
- `build_mac.sh`

## Easiest option

If you have a ready-made `.app` or `.zip` release, use that. That is the easiest option for normal users.

## Build on a Mac from this folder

If you only have this folder, you need Python installed on the Mac.

Open Terminal, go into this folder, and run:

```bash
chmod +x build_mac.sh
./build_mac.sh
```

The app will be created as:

```text
dist/TelegramGroupBackupDownload-v2.1.app
```

The app title inside the window should show:

```text
Telegram Group Backup & Download v2.1
```

To run it, open:

```text
dist/TelegramGroupBackupDownload-v2.1.app
```

## Build from GitHub

The GitHub workflow at:

```text
.github/workflows/build-macos.yml
```

uses this folder's build script and creates:

```text
TelegramGroupBackupDownload-v2.1-macOS.zip
```

## Note

The app is not Apple-signed or notarized. On first launch, Mac users may need to right-click the app and choose Open.

If macOS says the app is from an unidentified developer, right-click the app, choose Open, then confirm Open again.

Do not include Telegram session files, config files, state files, or downloaded media when sharing this folder.


