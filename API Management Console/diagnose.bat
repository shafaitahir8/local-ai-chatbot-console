@echo off
setlocal enabledelayedexpansion

echo ============================================
echo  Diagnostic Tool - AI Chatbot Console
echo ============================================
echo.

cd /d "%~dp0"
echo [1/8] Working directory: %CD%
echo.

echo [2/8] Checking Python...
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [FAIL] Python not found in PATH!
    goto :done
)
python --version
echo.

echo [3/8] Checking virtual environment...
if exist "venv\Scripts\python.exe" (
    echo [OK] Virtual environment exists
    echo Venv Python: venv\Scripts\python.exe
) else (
    echo [WARN] No virtual environment found
    echo Run: python -m venv venv
)
echo.

echo [4/8] Checking .env file...
if exist ".env" (
    echo [OK] .env file exists
    type .env | findstr /v "^#" | findstr /v "^$"
) else (
    echo [WARN] No .env file found
)
echo.

echo [5/8] Checking requirements.txt...
if exist "requirements.txt" (
    echo [OK] requirements.txt exists
) else (
    echo [FAIL] requirements.txt missing!
)
echo.

echo [6/8] Checking app structure...
if exist "app\__init__.py" (echo [OK] app\__init__.py) else (echo [FAIL] app\__init__.py missing)
if exist "app\main.py" (echo [OK] app\main.py) else (echo [FAIL] app\main.py missing)
if exist "app\models\database.py" (echo [OK] app\models\database.py) else (echo [FAIL] app\models\database.py missing)
if exist "app\routers\bots.py" (echo [OK] app\routers\bots.py) else (echo [FAIL] app\routers\bots.py missing)
echo.

echo [7/8] Testing Python imports (using venv if available)...
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

echo Testing basic imports...
python -c "import sys; print(f'Python: {sys.executable}')"
python -c "import fastapi; print(f'FastAPI: {fastapi.__version__}')" 2>&1
python -c "import uvicorn; print(f'Uvicorn: {uvicorn.__version__}')" 2>&1
python -c "import sqlalchemy; print(f'SQLAlchemy: {sqlalchemy.__version__}')" 2>&1
echo.

echo Testing app import...
python -c "from app.main import app; print('[OK] App imported successfully')" 2>&1
if %ERRORLEVEL% neq 0 (
    echo [FAIL] App import failed! See error above.
)
echo.

echo [8/8] Checking port 8000 availability...
netstat -an | findstr ":8000" >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [WARN] Port 8000 is already in use!
    netstat -an | findstr ":8000"
) else (
    echo [OK] Port 8000 is available
)
echo.

echo [9/8] Checking logs directory...
if exist "logs" (
    echo [OK] Logs directory exists
    dir /b logs\*.log 2>nul
) else (
    echo [INFO] No logs directory yet (will be created on first run)
)
echo.

:done
echo ============================================
echo  Diagnostic complete
echo ============================================
pause
