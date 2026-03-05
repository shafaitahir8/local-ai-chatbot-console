@echo off
echo ========================================
echo   Starting Frontend Development Server
echo ========================================
echo.
echo This runs the React frontend with hot-reload.
echo Make sure the backend server is also running on port 8000.
echo.

cd /d "%~dp0frontend"

:: Check if node is installed
where node >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Node.js is not installed or not in PATH.
    echo Please install Node.js from https://nodejs.org/
    pause
    exit /b 1
)

:: Check if node_modules exists
if not exist "node_modules" (
    echo Installing dependencies first...
    call npm install
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
    echo.
)

echo Starting development server at http://localhost:3000
echo.
call npm run dev
