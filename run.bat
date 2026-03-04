@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo ==================================================
echo   Weekly News Auto Curation
echo ==================================================
echo.

py main.py --articles-only

echo.
echo Done. Check output folder.
pause
