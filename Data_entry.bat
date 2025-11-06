@echo off
setlocal enabledelayedexpansion

:: ====== Force UTF-8 for CMD and Python ======
chcp 65001 >nul
set PYTHONUTF8=1

:: ====== CONFIG ======
set WORKDIR=C:\KSS_Working
set VENV=%WORKDIR%\venv
set LOGDIR=%WORKDIR%\Data Entry Log

:: ====== Date & Time ======
for /f "tokens=2-4 delims=/ " %%a in ('date /t') do (
    set mm=%%a
    set dd=%%b
    set yyyy=%%c
)

for /f "tokens=1-2 delims=: " %%a in ("%time%") do (
    set hh=%%a
    set mn=%%b
)

:: Zero-pad hours if needed
if %hh% lss 10 set hh=0%hh%

set DATESTR=%yyyy%-%mm%-%dd%
set TIMESTR=%hh%:%mn%

:: ====== Ensure log folder exists ======
if not exist "%LOGDIR%" (
    mkdir "%LOGDIR%"
)

set LOGFILE=%LOGDIR%\%DATESTR%.txt

:: ====== Header for new file ======
if not exist "%LOGFILE%" (
    echo ===== Data Entry Log for %DATESTR% ===== > "%LOGFILE%"
)

:: ====== Activate venv and run ======
echo [%TIMESTR%] Starting main.py >> "%LOGFILE%"

cd /d "%WORKDIR%"
call "%VENV%\Scripts\activate.bat"

python main.py >> "%LOGFILE%" 2>&1

echo [%TIMESTR%] Execution finished. >> "%LOGFILE%"
echo. >> "%LOGFILE%"

endlocal
exit /b
