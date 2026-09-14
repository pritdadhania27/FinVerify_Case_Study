@echo off
REM ===================================================================
REM  THE ONE-SHOT TEST EVALUATION. Read this before double-clicking.
REM
REM  THE SPLIT IS ALREADY SPENT. It was evaluated once on 2026-09-05
REM  (campaign_20260905T112212Z, 427 rows, RX-039). This script will
REM  now REFUSE to start, and that is correct - see below.
REM
REM  The test split is evaluated ONCE per frozen methodology version.
REM  methodology-freeze-v1 is VOID (D46): it froze Channel A to a model
REM  the provider retired two days later. There is no live freeze, so
REM  run_campaign.py refuses the test split outright.
REM
REM  That gate is new (D48). The 2026-09-05 run went ahead against the
REM  voided tag because nothing checked - D46 wrote its precondition as
REM  prose in a decision entry, and prose blocks nothing.
REM
REM  To evaluate the test split again you would have to: re-run
REM  validation on the current binding, re-select the threshold from it,
REM  and cut a new live methodology-freeze tag. Doing that means the
REM  split is used TWICE and the write-up must say so.
REM
REM  Access is gated by FINVERIFY_ALLOW_TEST and is LOGGED. That is
REM  deliberate: the log is the evidence the test set was touched once.
REM
REM  Cost: 61 questions x 7 arms = 427 rows, 1,037 requests. That is
REM  above the 1,000/day pacing placeholder, so it may stop on quota
REM  and need one resume - which is not a failure.
REM
REM  USAGE
REM    RUN_TEST_SPLIT.bat check          verify setup, spend nothing
REM    RUN_TEST_SPLIT.bat                start the evaluation
REM    RUN_TEST_SPLIT.bat <run_id>       resume a stopped one
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
echo   python:  %PY%

echo.
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

set "FINVERIFY_ALLOW_TEST=1"
set "LLM_MAX_TOKENS=4096"
set "REASON=final one-shot evaluation against methodology-freeze-v1"

if /i "%~1"=="check" (
    echo.
    echo   === CHECK MODE - costs nothing ===
    echo.
    "%PY%" scripts\run_campaign.py --arms A B5 G H B1 B2 B4 --split test --reason "%REASON%" --budget-only
    echo.
    echo   Setup is good. Run with no arguments to start the real evaluation.
    echo.
    pause
    exit /b 0
)

echo.
echo   ==================================================================
echo    THE TEST SPLIT IS ABOUT TO BE EVALUATED. THIS IS THE ONE SHOT.
echo    61 questions x 7 arms, 1,037 requests, roughly 3-4 hours.
echo    LEAVE THIS WINDOW OPEN. Ctrl+C stops it safely and it resumes.
echo   ==================================================================
echo.
echo   Press Ctrl+C now to abort, or
pause

if "%~1"=="" (
    echo   starting a NEW test run...
    "%PY%" scripts\run_campaign.py --arms A B5 G H B1 B2 B4 --split test --reason "%REASON%"
) else (
    echo   resuming %~1 ...
    "%PY%" scripts\run_campaign_unattended.py --resume %~1 --arms A B5 G H B1 B2 B4 --split test --max-tokens 4096
)

echo.
echo   ==================================================================
echo    Exit code %ERRORLEVEL%.  0 = finished.  3 = stopped on quota.
echo    If it stopped on quota, note the run id printed above and
echo    re-run:   RUN_TEST_SPLIT.bat ^<run_id^>
echo   ==================================================================
echo.
pause
