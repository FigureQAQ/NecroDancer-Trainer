@echo off
cd /d "%~dp0"
set "TRAINER_EXE=%~dp0dist\NecroDancerTrainer_Invincible.exe"

if exist "%TRAINER_EXE%" (
    start "" "%TRAINER_EXE%"
    exit /b 0
)

where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0NecroDancerModifier.py"
    exit /b %errorlevel%
)

where python >nul 2>nul
if not errorlevel 1 (
    python "%~dp0NecroDancerModifier.py"
    exit /b %errorlevel%
)

echo NecroDancer Trainer could not be started.
echo The packaged application is missing and Python was not found.
echo Reinstall the release package or install Python 3 with Pillow.
pause
exit /b 1
