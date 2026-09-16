@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title ODDIUM - PUSH GITHUB SECURISE

echo ==============================================
echo        ODDIUM - PUSH GITHUB SECURISE
echo ==============================================
echo.

for /f "delims=" %%i in ('git rev-parse --show-toplevel 2^>nul') do set "ROOT=%%i"
if not defined ROOT (
  echo ERREUR: ce dossier n'est pas un depot Git.
  echo Lance d'abord: git init
  pause
  exit /b 1
)

for %%i in ("%CD%") do set "HERE=%%~fi"
for %%i in ("%ROOT%") do set "ROOTABS=%%~fi"
if /I not "!HERE!"=="!ROOTABS!" (
  echo ERREUR DE SECURITE: la racine Git est !ROOTABS!
  echo Le bot est dans !HERE!
  echo Refus d'envoyer AppData/Documents ou un depot parent par erreur.
  pause
  exit /b 1
)

for /f "delims=" %%i in ('git remote get-url origin 2^>nul') do set "ORIGIN=%%i"
if not defined ORIGIN (
  echo ERREUR: aucun remote origin configure.
  echo Exemple: git remote add origin https://github.com/TON-COMPTE/Oddium.git
  pause
  exit /b 1
)

echo Remote: !ORIGIN!
echo !ORIGIN! | findstr /I "oddium" >nul
if errorlevel 1 (
  echo ERREUR DE SECURITE: origin ne ressemble pas au depot Oddium.
  echo Corrige avec: git remote set-url origin https://github.com/TON-COMPTE/Oddium.git
  pause
  exit /b 1
)

echo [1/4] Ajout des fichiers...
git add -A || goto :error

echo [2/4] Commit...
git diff --cached --quiet
if errorlevel 1 (
  git commit -m "Oddium update" || goto :error
) else (
  echo Aucun changement a commit.
)

echo [3/4] Rebase GitHub...
git pull --rebase origin main || goto :error

echo [4/4] Push...
git push origin main || goto :error

echo.
echo SUCCES: Oddium a ete envoye sur GitHub.
pause
exit /b 0

:error
echo.
echo Une erreur Git est survenue. Rien n'a ete force.
pause
exit /b 1
