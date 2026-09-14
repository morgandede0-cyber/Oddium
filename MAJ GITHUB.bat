@echo off
title PUSH ODDIUM -> GITHUB
echo ==========================================
echo        ENVOI ODDIUM SUR GITHUB
echo ==========================================
echo.

REM Place ce fichier directement dans le dossier racine du bot Oddium.
cd /d "%~dp0"

echo [1/5] Verification du depot Git...
git rev-parse --show-toplevel
if errorlevel 1 (
    echo.
    echo ERREUR : ce dossier n'est pas un depot Git.
    pause
    exit /b 1
)

echo.
echo [2/5] Ajout de tous les fichiers...
git add -A
if errorlevel 1 goto :error

echo.
echo [3/5] Creation du commit...
git diff --cached --quiet
if %errorlevel%==0 (
    echo Aucun changement a commit.
) else (
    git commit -m "Mise a jour Oddium"
    if errorlevel 1 goto :error
)

echo.
echo [4/5] Recuperation des changements GitHub...
git pull --rebase origin main
if errorlevel 1 goto :error

echo.
echo [5/5] Envoi sur GitHub...
git push origin main
if errorlevel 1 goto :error

echo.
echo ==========================================
echo       ODDIUM ENVOYE AVEC SUCCES
echo ==========================================
pause
exit /b 0

:error
echo.
echo ==========================================
echo UNE ERREUR EST SURVENUE.
echo Lis le message Git affiche juste au-dessus.
echo ==========================================
pause
exit /b 1
