@echo off
rem Pulse Console - 直播数据监听与 AI 陪伴中台 - 快捷启动
cd /d "d:\DouYin_Spider"
start "Pulse Console Server" "d:\DouYin_Spider\.venv\Scripts\python.exe" -m pulse.server
timeout /t 2 /nobreak >nul
start "" "http://localhost:8765"
