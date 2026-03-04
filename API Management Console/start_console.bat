@echo off
setlocal enabledelayedexpansion

REM === Local AI Chatbot Deployment Console Launcher ===
REM This script handles everything: venv, dependencies, Ollama, and server startup

title Local AI Chatbot Console

REM Set log file with proper date format
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set "datetime=%%I"
set "LOGFILE=%~dp0logs\startup_%datetime:~0,8%_%datetime:~8,6%.log"

REM Create logs directory
if not exist "%~dp0logs" mkdir "%~dp0logs"

REM Start logging
echo ============================================ > "%LOGFILE%"
echo  Startup Log - %date% %time% >> "%LOGFILE%"
echo ============================================ >> "%LOGFILE%"

echo.
echo  ============================================
echo   Local AI Chatbot Deployment Console
echo  ============================================
echo.
echo  [INFO] Log file: %LOGFILE%
echo.

REM Change to script directory
cd /d "%~dp0"
echo [INFO] Working directory: %CD% >> "%LOGFILE%"

REM ==================== STEP 1: Python Check ====================
echo [1/6] Checking Python installation...
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    echo [ERROR] Python not found >> "%LOGFILE%"
    echo        Please install Python 3.10+ from https://python.org
    goto :error
)
for /f "tokens=*" %%i in ('python --version 2^>^&1') do set PYTHON_VER=%%i
echo        Found: %PYTHON_VER%
echo [INFO] %PYTHON_VER% >> "%LOGFILE%"

REM ==================== STEP 2: Virtual Environment ====================
echo [2/6] Setting up virtual environment...
if not exist "venv\Scripts\activate.bat" (
    echo        Creating new virtual environment...
    echo [SETUP] Creating venv >> "%LOGFILE%"
    python -m venv venv >> "%LOGFILE%" 2>&1
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to create virtual environment!
        echo [ERROR] venv creation failed >> "%LOGFILE%"
        goto :error
    )
    echo        Virtual environment created.
) else (
    echo        Virtual environment exists.
)

REM Activate virtual environment
call venv\Scripts\activate.bat
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to activate virtual environment!
    goto :error
)
echo [INFO] venv activated >> "%LOGFILE%"

REM ==================== STEP 3: Dependencies ====================
echo [3/6] Checking dependencies...
set "INSTALL_FLAG=venv\.requirements_installed"
if not exist "%INSTALL_FLAG%" (
    echo        Installing dependencies - first time setup...
    echo [SETUP] Installing dependencies >> "%LOGFILE%"
    pip install --upgrade pip >> "%LOGFILE%" 2>&1
    pip install -r requirements.txt >> "%LOGFILE%" 2>&1
    if !ERRORLEVEL! neq 0 goto :depserror
    echo. > "%INSTALL_FLAG%"
    echo        Dependencies installed.
) else (
    echo        Dependencies already installed.
)
goto :step4

:depserror
echo [ERROR] Failed to install dependencies!
echo [ERROR] pip install failed >> "%LOGFILE%"
goto :error

:step4

REM ==================== STEP 4: Environment Variables ====================
echo [4/6] Loading configuration...
if exist ".env" (
    REM Simple env loading - just read key=value pairs
    for /f "eol=# usebackq tokens=1,* delims==" %%a in (".env") do (
        if not "%%a"=="" if not "%%b"=="" set "%%a=%%b"
    )
    echo        Configuration loaded from .env
) else (
    echo [WARN] No .env file found - using defaults
)

REM Create data directories
if not exist "data" mkdir data
if not exist "data\bots" mkdir data\bots

REM ==================== STEP 5: Ollama (Optional) ====================
echo [5/6] Checking Ollama (local LLM)...
where ollama >nul 2>&1
if %ERRORLEVEL% equ 0 (
    REM Check if Ollama is running using PowerShell (more reliable on Windows)
    powershell -Command "try { $null = Invoke-WebRequest -Uri 'http://localhost:11434/api/tags' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>&1
    if %ERRORLEVEL% neq 0 (
        echo        Starting Ollama server...
        start "" /B ollama serve >nul 2>&1
        REM Wait for Ollama to start
        powershell -Command "Start-Sleep -Seconds 3" >nul 2>&1
    ) else (
        echo        Ollama is running.
    )
    
    REM Get model count using PowerShell (more reliable)
    for /f %%i in ('powershell -Command "(ollama list 2>$null | Select-Object -Skip 1 | Measure-Object).Count"') do set "MODEL_COUNT=%%i"
    if !MODEL_COUNT! GTR 0 (
        echo        Found !MODEL_COUNT! installed model^(s^).
    ) else (
        echo [WARN] No Ollama models installed.
        echo        Run setup_ollama.bat to install models.
    )
    echo [INFO] Ollama ready, !MODEL_COUNT! models >> "%LOGFILE%"
) else (
    echo [WARN] Ollama not installed - local LLM disabled
    echo        Install from https://ollama.ai for local models
    echo [WARN] Ollama not found >> "%LOGFILE%"
)

REM ==================== STEP 6: Start Server ====================
echo [6/6] Starting server...
echo.

REM Test app import first
python -c "from app.main import app; print('OK')" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to load application!
    echo [ERROR] App import failed >> "%LOGFILE%"
    python -c "from app.main import app" >> "%LOGFILE%" 2>&1
    goto :error
)

echo  ============================================
echo   Server Starting...
echo  ============================================
echo.
echo   Admin Dashboard:  http://localhost:8000/admin
echo   API Docs:         http://localhost:8000/docs
echo.
echo   Press CTRL+C to stop the server
echo  ============================================
echo.

echo [INFO] Starting uvicorn >> "%LOGFILE%"

REM Open browser after delay using a hidden PowerShell script
start "" /B powershell -WindowStyle Hidden -Command "Start-Sleep -Seconds 4; Start-Process 'http://localhost:8000/admin'"

REM Start the server (this blocks until CTRL+C)
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
set SERVER_EXIT=%ERRORLEVEL%

if %SERVER_EXIT% neq 0 if %SERVER_EXIT% neq 3221225786 (
    echo.
    echo [ERROR] Server crashed! Exit code: %SERVER_EXIT%
    echo [ERROR] Server exit: %SERVER_EXIT% >> "%LOGFILE%"
    goto :error
)

goto :end

:error
echo.
echo  ============================================
echo   STARTUP FAILED
echo  ============================================
echo.
echo  Check the log file for details:
echo  %LOGFILE%
echo.
pause
exit /b 1

:end
echo.
echo [INFO] Server stopped >> "%LOGFILE%"
echo  Server stopped. Goodbye!
pause
