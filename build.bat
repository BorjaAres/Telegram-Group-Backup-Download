@echo off
cd /d "%~dp0"
set "ROOT=%~dp0..\.."

echo Installing dependencies...
python -m pip install telethon pyinstaller --quiet

echo Building executable...
python -m PyInstaller ^
  --onefile ^
  --windowed ^
  --name "TelegramGroupBackupDownload" ^
  --hidden-import tg_shared ^
  --hidden-import tg_telegram ^
  --hidden-import tg_workers ^
  --hidden-import tg_ui ^
  --add-data "Telegram Group Backup & Download v1.1 Guide.pdf;." ^
  tg_backup_gui.py

echo.
if exist dist\TelegramGroupBackupDownload.exe (
    copy /Y "dist\TelegramGroupBackupDownload.exe" "%ROOT%\TelegramGroupBackupDownload.exe" >nul
    echo SUCCESS! Your executable is ready at: %ROOT%\TelegramGroupBackupDownload.exe
) else (
    echo FAILED - check errors above
)
pause
