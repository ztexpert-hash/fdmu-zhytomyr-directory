@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Оновлення бази ФДМУ Житомир v6.0...
python fdmu_auto_update_v6.py
pause
