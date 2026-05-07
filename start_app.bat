@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo     B-Workflow SQL
echo ========================================

echo [1/2] Checking Python...
python --version
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.10+
    pause
    exit /b 1
)

echo [2/2] Launching...
python run.py
if errorlevel 1 (
    echo.
    echo ===== LAUNCH FAILED =====
    echo Please check the error message above.
    pause
    exit /b 1
)
