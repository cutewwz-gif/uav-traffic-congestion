@echo off
cd /d "%~dp0"

echo ========================================
echo  UAV Traffic Congestion Analyzer
echo  Default: http://localhost:8000
echo  (auto-fallback if port busy)
echo ========================================
echo.

python app.py
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to start. Check Python PATH or free ports 8000+.
    pause
)
