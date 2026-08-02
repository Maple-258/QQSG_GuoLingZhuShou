@echo off
setlocal
set "PROJECT_DIR=%~dp0.."
for %%I in ("%PROJECT_DIR%") do set "PROJECT_DIR=%%~fI"
cd /d "%PROJECT_DIR%"
set "PYTHONW=%PROJECT_DIR%\.venv\Scripts\pythonw.exe"

if not exist "%PYTHONW%" goto :missing_venv

start "GuoLingZhuShou" /D "%PROJECT_DIR%" "%PYTHONW%" -m guoling_task_ocr
exit /b 0

:missing_venv
echo ERROR: Virtual environment was not found.
echo Run the dependency installation script first.
pause
exit /b 1
