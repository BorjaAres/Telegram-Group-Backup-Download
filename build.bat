@echo off
echo Installing dependencies...
python -m pip install telethon pyinstaller --quiet

echo Building executable...
python -m PyInstaller --onefile --windowed --name "TelegramGroupBackupDownload" tg_backup_gui.py

echo.
if exist dist\TelegramGroupBackupDownload.exe (
    echo SUCCESS! Your executable is ready at: dist\TelegramGroupBackupDownload.exe
) else (
    echo FAILED - check errors above
)
pause
