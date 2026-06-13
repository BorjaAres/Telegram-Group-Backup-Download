@echo off
cd /d "%~dp0"
set "ROOT=%~dp0..\.."
set "PYTHON_CMD="

where python >nul 2>nul
if %errorlevel%==0 set "PYTHON_CMD=python"

if not defined PYTHON_CMD (
    where py >nul 2>nul
    if %errorlevel%==0 set "PYTHON_CMD=py -3"
)

if not defined PYTHON_CMD (
    echo Python was not found.
    echo Install Python 3, then run this build.bat again.
    pause
    exit /b 1
)

echo Installing dependencies...
%PYTHON_CMD% -m pip install telethon pyinstaller --quiet
if errorlevel 1 (
    echo FAILED - could not install build dependencies.
    pause
    exit /b 1
)

echo Building executable...
%PYTHON_CMD% -m PyInstaller ^
  --onefile ^
  --windowed ^
  --name "TelegramGroupBackupDownload-v2.1" ^
  --hidden-import tg_shared ^
  --hidden-import tg_telegram ^
  --hidden-import tg_workers ^
  --hidden-import tg_ui ^
  --add-data "Telegram Group Backup & Download v2.1 Guide.pdf;." ^
  tg_backup_gui.py

echo.
if exist "dist\TelegramGroupBackupDownload-v2.1.exe" (
    copy /Y "dist\TelegramGroupBackupDownload-v2.1.exe" "%ROOT%\TelegramGroupBackupDownload-v2.1.exe" >nul
    echo SUCCESS! Your executable is ready at:
    echo   %ROOT%\TelegramGroupBackupDownload-v2.1.exe
) else (
    echo FAILED - check errors above
)
pause

