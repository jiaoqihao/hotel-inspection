@echo off
cd /d "%~dp0"
"%LOCALAPPDATA%\HotelVoice\venv\Scripts\pythonw.exe" -m local.badge_sync
