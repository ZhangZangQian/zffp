@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   Ju-Yin Breakout - Review & Discipline
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found in PATH.
  echo Install Python 3.10+ and try again.
  pause
  exit /b 1
)

python -c "import fastapi, uvicorn, requests" >nul 2>nul
if errorlevel 1 (
  echo [INFO] Installing dependencies...
  python -m pip install -r requirements.txt
)

echo [INFO] Starting on http://127.0.0.1:8777
echo [INFO] Press Ctrl+C to stop.
echo.
python run.py %*

pause
