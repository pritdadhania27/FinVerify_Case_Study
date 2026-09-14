@echo off
REM ===================================================================
REM  The oracle-retrieval diagnostic (arm O), VALIDATION SPLIT ONLY.
REM
REM  Hands each question the chunks containing its gold evidence, so
REM  every remaining error is a reasoning error by construction. That
REM  is the stratum H2 needs and the end-to-end system cannot supply:
REM  retrieval delivered every gold group on 29% of validation
REM  questions, and the reasoning stratum held zero errors.
REM
REM  Oracle resolves for 37 of 45 validation questions. It was 43 before
REM  the anchor repair (RX-042) - but up to 10 of those 43 resolved
REM  through STALE anchors and handed the channels the wrong chunk. 37
REM  trustworthy is worth more than 43 partly wrong, and this is the
REM  first run whose oracle evidence is actually correct.
REM
REM  WHY NOT THE TEST SPLIT. This arm was designed AFTER seeing the
REM  test result (RX-039's H2 failure). Running it on test would be a
REM  second evaluation of a held-out set, chosen in response to what
REM  that set already showed - which is the leakage the one-shot rule
REM  exists to prevent. It is a validation-stage diagnostic and is
REM  reported as one.
REM
REM  Cost: 45 questions x 3 calls = 135 requests, roughly 30-45 min.
REM
REM  USAGE
REM    RUN_ORACLE.bat check       verify setup, spend nothing
REM    RUN_ORACLE.bat             run it
REM    RUN_ORACLE.bat <run_id>    resume a stopped one
REM ===================================================================

cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"

echo.
echo   project: %CD%
if not exist "%PY%" (
    echo.
    echo   ERROR: no venv interpreter at %PY%
    echo.
    pause
    exit /b 1
)

echo   starting Docker services...
docker compose up -d
if errorlevel 1 (
    echo.
    echo   ERROR: docker compose failed. Is Docker Desktop running?
    echo.
    pause
    exit /b 1
)

echo   waiting for Qdrant to report healthy...
set /a TRIES=0
:waitloop
docker compose ps --format "{{.Service}} {{.Status}}" | findstr /C:"qdrant" | findstr /C:"healthy" >nul
if not errorlevel 1 goto ready
set /a TRIES+=1
if %TRIES% GEQ 30 (
    echo.
    echo   ERROR: Qdrant did not become healthy after 60 seconds.
    echo.
    pause
    exit /b 1
)
timeout /t 2 /nobreak >nul
goto waitloop
:ready
echo   Qdrant is healthy.

set "LLM_MAX_TOKENS=4096"

if /i "%~1"=="check" (
    echo.
    echo   === CHECK MODE - costs nothing ===
    echo.
    "%PY%" scripts\run_campaign.py --arms O --split validation --budget-only
    echo.
    echo   Setup is good. Run with no arguments to start.
    echo.
    pause
    exit /b 0
)

echo.
echo   ==================================================================
echo    Oracle diagnostic, arm O, validation split. 45 questions.
echo    Roughly 30-45 minutes. LEAVE THIS WINDOW OPEN.
echo   ==================================================================
echo.

if "%~1"=="" (
    "%PY%" scripts\run_campaign.py --arms O --split validation
) else (
    echo   resuming %~1 ...
    "%PY%" scripts\run_campaign_unattended.py --resume %~1 --arms O --max-tokens 4096
)

echo.
echo   ==================================================================
echo    Exit code %ERRORLEVEL%.  0 = finished.  3 = stopped on quota.
echo    If it stopped, re-run:  RUN_ORACLE.bat ^<run_id^>
echo   ==================================================================
echo.
pause
