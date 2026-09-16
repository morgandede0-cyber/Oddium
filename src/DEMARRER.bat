@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .env (
  echo ERREUR: fichier .env absent. Lance INSTALLER.bat d'abord.
  pause
  exit /b 1
)
where py >nul 2>&1
if %errorlevel%==0 (
  py main.py
) else (
  python main.py
)
pause
