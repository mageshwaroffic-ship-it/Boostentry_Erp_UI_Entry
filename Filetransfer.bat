@echo off
REM ============================================
REM Run gfiledownload.py inside venv and log output
REM ============================================

:: Force console to UTF-8 (prevents UnicodeEncodeError)
chcp 65001 >nul

:: Set base log folder
set LOG_DIR=C:\KSS_Working\logs

:: Ensure log directory exists
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

:: Get date in YYYYMMDD format
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set ldt=%%I
set DATESTAMP=%ldt:~0,8%

:: Build log filename (daily log)
set LOG_FILE=%LOG_DIR%\%DATESTAMP%.txt

:: Add header with timestamp (HH:MM:SS)
set TIMEONLY=%ldt:~8,2%:%ldt:~10,2%:%ldt:~12,2%
echo ============================== >> "%LOG_FILE%"
echo Run at %DATESTAMP% %TIMEONLY% >> "%LOG_FILE%"
echo ============================== >> "%LOG_FILE%"

:: Run Python script directly from venv
C:\KSS_Working\venv\Scripts\python.exe C:\KSS_Working\gfiledownload.py >> "%LOG_FILE%" 2>&1

:: Add blank line after run
echo. >> "%LOG_FILE%"
