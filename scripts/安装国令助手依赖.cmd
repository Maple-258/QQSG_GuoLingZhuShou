@echo off
setlocal
set "PROJECT_DIR=%~dp0.."
for %%I in ("%PROJECT_DIR%") do set "PROJECT_DIR=%%~fI"
cd /d "%PROJECT_DIR%"
set "VENV_DIR=%PROJECT_DIR%\.venv"
set "LOG_FILE=%PROJECT_DIR%\install_ocr.log"

> "%LOG_FILE%" echo ==== OCR dependency installation started %date% %time% ====
call :install >> "%LOG_FILE%" 2>&1
set "RESULT=%ERRORLEVEL%"

echo.
type "%LOG_FILE%"
echo.
if not "%RESULT%"=="0" (
  echo Installation failed. See install_ocr.log in the project root.
) else (
  echo Installation completed. Run scripts\启动国令助手.cmd next.
)
echo.
pause
exit /b %RESULT%

:install
py -3.11 -c "import sys; print('Using Python', sys.version)"
if errorlevel 1 (
  echo ERROR: Python 3.11 was not found.
  echo Install 64-bit Python 3.11 first. PaddlePaddle does not support Python 3.14.
  exit /b 1
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
  echo Creating project virtual environment .venv ...
  py -3.11 -m venv "%VENV_DIR%"
  if errorlevel 1 exit /b 1
)

echo Installing OCR dependencies into .venv ...
"%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
"%VENV_DIR%\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
"%VENV_DIR%\Scripts\python.exe" -m pip install -e .
exit /b %ERRORLEVEL%
