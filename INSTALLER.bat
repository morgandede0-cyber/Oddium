@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
  py -m pip install --upgrade pip
  py -m pip install -r requirements.txt
) else (
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
)
if not exist .env copy .env.example .env >nul
echo.
echo Installation terminee.
echo Ouvre maintenant le fichier .env et renseigne DISCORD_TOKEN et FOOTBALL_DATA_API_KEY.
pause
