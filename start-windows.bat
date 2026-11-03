@echo off
rem Starts Move Route Weather and opens it in your browser. Close this window to stop it.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py server.py
) else (
  python server.py
)
if errorlevel 1 (
  echo.
  echo Could not start. Install Python 3 from https://www.python.org/downloads/ and try again.
)
pause
