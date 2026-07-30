@echo off
setlocal
set "PROJECT_DIR=%~dp0.."
for %%I in ("%PROJECT_DIR%") do set "PROJECT_DIR=%%~fI"
cd /d "%PROJECT_DIR%"
set "PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe"

if not exist "%PYTHON%" goto :missing_venv

"%PYTHON%" -m pip install -r requirements-build.txt
if errorlevel 1 goto :failed

"%PYTHON%" -m PyInstaller --noconfirm --clean --windowed --onefile --name GuoLingZhuShou-1.0.2 --paths "%PROJECT_DIR%\src" --distpath release --workpath build\pyinstaller --specpath build --add-data "%PROJECT_DIR%\src\guoling_task_ocr\data;guoling_task_ocr\data" --add-data "%PROJECT_DIR%\.venv\Lib\site-packages\Cython\Utility\CppSupport.cpp;Cython\Utility" --collect-all paddle --collect-all paddleocr --collect-all windows_capture --collect-data Cython "%PROJECT_DIR%\scripts\pyinstaller_entry.py"
if errorlevel 1 goto :failed

echo.
echo Build completed: release\GuoLingZhuShou-1.0.2.exe
pause
exit /b 0

:missing_venv
echo ERROR: Virtual environment was not found.
echo Run scripts\安装国令助手依赖.cmd first.
pause
exit /b 1

:failed
echo.
echo Build failed. Review the output above.
pause
exit /b 1
