@echo off
chcp 65001 >nul
cd /d "%~dp0"
"%LOCALAPPDATA%\HotelVoice\venv\Scripts\python.exe" local\launch.py
if errorlevel 1 pause
