@echo off
REM ===================================================================
REM  Resume the FinVerify-AI campaign. Double-click this file, or run
REM  it from any directory - it does not care where it is invoked from.
REM
REM  Every argument that has gone wrong before is baked in here instead
REM  of being typed:
REM    * the working directory (%~dp0 is this file's own folder)
REM    * the venv interpreter, NOT the .py file association, which on
REM      this machine is the system 3.12 and lacks langgraph
REM    * no line continuations, so there is no ^ vs ` to get wrong
REM    * Docker started first - Qdrant serves retrieval and the run
REM      fails outright without it
REM
REM  RUN_CAMPAIGN.bat check      verify the setup, spend nothing
REM  RUN_CAMPAIGN.bat            start a NEW validation campaign
REM  RUN_CAMPAIGN.bat <run_id>   resume a stopped one
REM ===================================================================

cd /d "%~dp0"
echo.
echo   project: %CD%

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo.
    echo   ERROR: no venv interpreter at
    echo          %PY%
    echo   Create it with: py -3.12 -m venv .venv
    echo.
    pause
    exit /b 1
)
echo   python:  %PY%

echo.
echo   starting Docker services ^(Qdrant for retrieval, PostgreSQL^)...
docker compose up -d
if errorlevel 1 (
    echo.
    echo   ERROR: docker compose failed. Is Docker Desktop running?
    echo   Start Docker Desktop, wait for it to say "Engine running", retry.
    echo.
    pause
    exit /b 1
)

echo.
echo   waiting for Qdrant to report healthy...
set /a TRIES=0
:waitloop
docker compose ps --format "{{.Service}} {{.Status}}" | findstr /C:"qdrant" | findstr /C:"healthy" >nul
if not errorlevel 1 goto ready
set /a TRIES+=1
if %TRIES% GEQ 30 (
    echo.
    echo   ERROR: Qdrant did not become healthy after 60 seconds.
    echo   Check: docker compose ps
    echo.
    pause
    exit /b 1
)
timeout /t 2 /nobreak >nul
goto waitloop

:ready
echo   Qdrant is healthy.

if /i "%~1"=="check" (
    echo.
    echo   === CHECK MODE - nothing will be spent ===
    echo.
    "%PY%" scripts\run_campaign_unattended.py --help
    echo.
    echo   Setup is good. Run this file with no arguments to start.
    echo.
    pause
    exit /b 0
)

echo.
echo   ==================================================================
echo    Validation campaign: seven arms x 45 questions = 315 rows.
echo    Roughly 2-3 hours. LEAVE THIS WINDOW OPEN.
echo    Ctrl+C stops it safely - re-run with the run id to continue.
echo   ==================================================================
echo.

if "%~1"=="" (
    echo   starting a NEW validation run...
    "%PY%" scripts\run_campaign.py --arms A B5 G H B1 B2 B4 --split validation
) else (
    echo   resuming %~1 ...
    "%PY%" scripts\run_campaign_unattended.py --resume %~1 --arms A B5 G H B1 B2 B4 --max-tokens 4096
)

echo.
echo   ==================================================================
echo    Campaign process ended with exit code %ERRORLEVEL%.
echo    0 = finished or deadline reached.  3 = stopped on quota, resumable.
echo    Re-run this file to continue; it never re-spends finished work.
echo   ==================================================================
echo.
pause
