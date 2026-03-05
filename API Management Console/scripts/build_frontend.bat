@echo off
echo ========================================
echo   Building Frontend UI
echo ========================================
echo.

cd /d "%~dp0frontend"

:: Check if node is installed
where node >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Node.js is not installed or not in PATH.
    echo Please install Node.js from https://nodejs.org/
    echo.
    echo The backend will still work with the fallback UI.
    pause
    exit /b 1
)

echo [1/3] Checking Node.js version...
node --version
echo.

:: Check if node_modules exists
if not exist "node_modules" (
    echo [2/3] Installing dependencies...
    call npm install
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
) else (
    echo [2/3] Dependencies already installed.
)
echo.

echo [3/3] Building production bundle...
call npm run build
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Build failed.
    pause
    exit /b 1
)
echo.

echo ========================================
echo   Build completed successfully!
echo   Output: frontend\dist
echo ========================================
echo.
echo The new React UI will now be served when you start the console.
pause
