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
echo [1/7] Checking Python installation...
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
echo [2/7] Setting up virtual environment...
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
echo [3/7] Checking dependencies...
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
echo [4/7] Loading configuration...
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

REM ==================== STEP 5: Public Tunnel ====================
echo [5/7] Checking public tunnel provider...
if not exist "tools" mkdir tools

if not defined TUNNEL_PROVIDER set "TUNNEL_PROVIDER=localtunnel"

if /I "%TUNNEL_PROVIDER%"=="localtunnel" (
    where npx >nul 2>&1
    if %ERRORLEVEL% neq 0 (
        echo [WARN] TUNNEL_PROVIDER=localtunnel but npx is not available. Falling back to cloudflare.
        echo [WARN] npx not found for localtunnel >> "%LOGFILE%"
        set "TUNNEL_PROVIDER=cloudflare"
    ) else (
        set "AUTO_CLOUDFLARE_TUNNEL=true"
        echo        LocalTunnel mode enabled via npx localtunnel.
        if defined LOCALTUNNEL_SUBDOMAIN (
            echo        Requested LocalTunnel subdomain: %LOCALTUNNEL_SUBDOMAIN%
            echo [INFO] localtunnel subdomain: %LOCALTUNNEL_SUBDOMAIN% >> "%LOGFILE%"
        ) else (
            echo [WARN] LOCALTUNNEL_SUBDOMAIN not set - URL may change on restart.
            echo [WARN] localtunnel subdomain missing >> "%LOGFILE%"
        )
        echo [INFO] tunnel provider: localtunnel >> "%LOGFILE%"
        goto :tunnel_provider_done
    )
)

if /I "%TUNNEL_PROVIDER%"=="ngrok" (
    where ngrok >nul 2>&1
    if %ERRORLEVEL% neq 0 (
        echo [WARN] TUNNEL_PROVIDER=ngrok but ngrok is not available. Falling back to cloudflare.
        echo [WARN] ngrok not found >> "%LOGFILE%"
        set "TUNNEL_PROVIDER=cloudflare"
    ) else (
        set "AUTO_CLOUDFLARE_TUNNEL=true"
        echo        ngrok mode enabled.
        if defined NGROK_STATIC_DOMAIN (
            echo        Requested ngrok static domain: %NGROK_STATIC_DOMAIN%
            echo [INFO] ngrok static domain: %NGROK_STATIC_DOMAIN% >> "%LOGFILE%"
        ) else (
            echo [WARN] NGROK_STATIC_DOMAIN not set - URL may change on restart.
            echo [WARN] ngrok static domain missing >> "%LOGFILE%"
        )
        if not defined NGROK_AUTHTOKEN (
            echo [WARN] NGROK_AUTHTOKEN not set. Reserved/static domains usually require authenticated ngrok.
            echo [WARN] ngrok authtoken missing >> "%LOGFILE%"
        )
        echo [INFO] tunnel provider: ngrok >> "%LOGFILE%"
        goto :tunnel_provider_done
    )
)

set "CLOUDFLARE_BIN="
if exist "tools\cloudflared.exe" (
    set "CLOUDFLARE_BIN=%CD%\tools\cloudflared.exe"
) else (
    where cloudflared >nul 2>&1
    if %ERRORLEVEL% equ 0 (
        for /f "tokens=*" %%i in ('where cloudflared') do (
            set "CLOUDFLARE_BIN=%%i"
            goto :cf_found
        )
    ) else (
        echo        cloudflared not found, downloading portable binary...
        powershell -Command "try { Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile 'tools\\cloudflared.exe'; exit 0 } catch { exit 1 }" >nul 2>&1
        if !ERRORLEVEL! equ 0 (
            set "CLOUDFLARE_BIN=%CD%\tools\cloudflared.exe"
            echo        cloudflared downloaded to tools\cloudflared.exe
        ) else (
            echo [WARN] Could not download cloudflared. Tunnel auto-start may be unavailable.
            echo [WARN] cloudflared download failed >> "%LOGFILE%"
        )
    )
)

:cf_found
if defined CLOUDFLARE_BIN (
    set "CLOUDFLARED_PATH=%CLOUDFLARE_BIN%"
    set "AUTO_CLOUDFLARE_TUNNEL=true"
    if defined CLOUDFLARE_TUNNEL_TOKEN (
        echo        Named Cloudflare tunnel mode enabled.
        if defined CLOUDFLARE_PUBLIC_BASE_URL (
            echo        Stable public URL: %CLOUDFLARE_PUBLIC_BASE_URL%
            echo [INFO] stable tunnel URL: %CLOUDFLARE_PUBLIC_BASE_URL% >> "%LOGFILE%"
        ) else (
            echo [WARN] CLOUDFLARE_TUNNEL_TOKEN set, but CLOUDFLARE_PUBLIC_BASE_URL is missing.
            echo        Add CLOUDFLARE_PUBLIC_BASE_URL in .env to keep a fixed endpoint in UI.
            echo [WARN] stable URL missing for named tunnel >> "%LOGFILE%"
        )
    ) else (
        echo        Quick tunnel mode enabled ^(URL changes each restart^).
    )
    echo        Cloudflare tunnel binary: %CLOUDFLARE_BIN%
    echo [INFO] cloudflared path: %CLOUDFLARE_BIN% >> "%LOGFILE%"
) else (
    echo [WARN] Cloudflare tunnel binary unavailable - public endpoint may be missing.
    echo [WARN] cloudflared unavailable >> "%LOGFILE%"
)

:tunnel_provider_done
REM ==================== STEP 6: Ollama (Optional) ====================
echo [6/7] Checking Ollama (local LLM)...
where ollama >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "OLLAMA_READY=0"
    set "MODEL_COUNT=0"

    REM Check if Ollama is already running
    powershell -Command "try { $null = Invoke-WebRequest -Uri 'http://localhost:11434/api/tags' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>&1
    if %ERRORLEVEL% equ 0 (
        set "OLLAMA_READY=1"
        echo        Ollama is running.
    ) else (
        echo        Starting Ollama server in background...
        start "" /MIN cmd /c "ollama serve >nul 2>&1"

        REM Poll readiness up to 25 seconds so startup never hangs indefinitely
        for /l %%s in (1,1,25) do (
            if !OLLAMA_READY! equ 0 (
                powershell -Command "try { $null = Invoke-WebRequest -Uri 'http://localhost:11434/api/tags' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>&1
                if !ERRORLEVEL! equ 0 set "OLLAMA_READY=1"
            )
            if !OLLAMA_READY! equ 0 (
                powershell -Command "Start-Sleep -Seconds 1" >nul 2>&1
            )
        )
    )
    if !OLLAMA_READY! equ 1 (
        for /f %%i in ('powershell -Command "try { $r = Invoke-RestMethod -Uri ''http://localhost:11434/api/tags'' -TimeoutSec 3; if($r.models){$r.models.Count}else{0} } catch { 0 }"') do set "MODEL_COUNT=%%i"
        if !MODEL_COUNT! GTR 0 (
            echo        Found !MODEL_COUNT! installed model^(s^).
        ) else (
            echo [WARN] Ollama running but no models installed.
            echo        Run scripts\setup_ollama.bat to install models.
        )
        echo [INFO] Ollama ready, !MODEL_COUNT! models >> "%LOGFILE%"
    ) else (
        echo [WARN] Ollama start timed out. Server will continue; local bots may fail until Ollama is ready.
        echo [WARN] Ollama readiness timeout >> "%LOGFILE%"
    )
) else (
    echo [WARN] Ollama not installed - local LLM disabled
    echo        Install from https://ollama.ai for local models
    echo [WARN] Ollama not found >> "%LOGFILE%"
    echo        To install models later, run scripts\setup_ollama.bat
)

REM ==================== STEP 7: Start Server ====================
echo [7/7] Starting server...
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
