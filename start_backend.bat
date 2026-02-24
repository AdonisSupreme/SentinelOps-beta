@echo off
echo Starting SentinelOps Backend Server...
echo.

REM Activate virtual environment
call sentinel\Scripts\activate.bat

REM Check if activation was successful
if %ERRORLEVEL% NEQ 0 (
    echo Failed to activate virtual environment
    pause
    exit /b 1
)

echo Virtual environment activated successfully
echo.

REM Start the server
echo Starting FastAPI server on http://localhost:8000
echo WebSocket endpoint will be available at: ws://localhost:8000/api/v1/checklists/ws
echo.
echo Press Ctrl+C to stop the server
echo.

uvicorn app.main:app --reload

pause
