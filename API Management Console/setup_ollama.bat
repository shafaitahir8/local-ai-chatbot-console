@echo off
setlocal enabledelayedexpansion

REM === Ollama Model Setup for Local AI Chatbot Console ===
REM This script installs recommended Ollama models (ONLY if not already installed)
REM Models are stored in: %USERPROFILE%\.ollama\models
REM Run once after installing Ollama from https://ollama.ai

title Ollama Model Setup

echo.
echo  ============================================
echo   Ollama Model Setup
echo  ============================================
echo.

REM Check if Ollama is installed
where ollama >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] Ollama is not installed!
    echo.
    echo  Please download and install Ollama from:
    echo    https://ollama.ai
    echo.
    echo  After installation, run this script again.
    echo.
    pause
    exit /b 1
)

echo  [OK] Ollama is installed
echo.

REM Check if Ollama is running
powershell -Command "try { $null = Invoke-WebRequest -Uri 'http://localhost:11434/api/tags' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo  [INFO] Starting Ollama server...
    start "" /B ollama serve >nul 2>&1
    powershell -Command "Start-Sleep -Seconds 5" >nul
)

REM Get list of installed models
echo  [INFO] Checking installed models...
echo.

REM Store installed models in a temp file
ollama list > "%TEMP%\ollama_models.txt" 2>nul

REM Define required models
set "MODELS=qwen2.5:1.5b llama3.2:1b llama3.2:3b phi3:mini gemma2:2b"
set NEED_INSTALL=0
set ALREADY_INSTALLED=0

echo  Currently installed models:
echo  ---------------------------
type "%TEMP%\ollama_models.txt"
echo.

echo  Recommended models for this console:
echo  ------------------------------------

REM Check each model
for %%m in (%MODELS%) do (
    findstr /i "%%m" "%TEMP%\ollama_models.txt" >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        echo    [INSTALLED] %%m
        set /a ALREADY_INSTALLED+=1
    ) else (
        echo    [MISSING]   %%m
        set /a NEED_INSTALL+=1
    )
)

echo.

REM Clean up temp file
del "%TEMP%\ollama_models.txt" 2>nul

REM Check if all models are installed
if %NEED_INSTALL% equ 0 (
    echo  ============================================
    echo   All recommended models are installed!
    echo  ============================================
    echo.
    echo  You can now use local LLM models in the console.
    echo  Select "Local LLM" as provider when creating bots.
    echo.
    pause
    exit /b 0
)

echo  Models already installed: %ALREADY_INSTALLED%
echo  Models to install: %NEED_INSTALL%
echo.
echo  ============================================
echo   Installing Missing Models
echo  ============================================
echo.
echo  Note: Models are stored locally in:
echo    %USERPROFILE%\.ollama\models
echo.
echo  Press any key to install missing models...
echo  (or CTRL+C to cancel)
pause >nul
echo.

REM Install missing models
set INSTALLED=0
set FAILED=0

for %%m in (%MODELS%) do (
    findstr /i "%%m" "%TEMP%\ollama_models_check.txt" >nul 2>&1
    
    REM Re-check if model exists
    ollama list 2>nul | findstr /i "%%m" >nul 2>&1
    if !ERRORLEVEL! neq 0 (
        echo  [INSTALLING] %%m...
        ollama pull %%m
        if !ERRORLEVEL! equ 0 (
            echo  [OK] %%m installed successfully
            set /a INSTALLED+=1
        ) else (
            echo  [FAILED] Could not install %%m
            set /a FAILED+=1
        )
        echo.
    ) else (
        echo  [SKIP] %%m already installed
    )
)

echo.
echo  ============================================
echo   Setup Complete
echo  ============================================
echo.
echo  Installed: %INSTALLED% new model(s)
if %FAILED% gtr 0 (
    echo  Failed: %FAILED% model(s)
)
echo.
echo  [INFO] Current installed models:
ollama list
echo.
echo  You can now use local LLM models in the console.
echo  Select "Local LLM" as provider when creating bots.
echo.
pause
