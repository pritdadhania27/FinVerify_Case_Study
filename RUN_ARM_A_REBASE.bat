@echo off
REM ===================================================================
REM  ARM A ON THE CURRENT BINDING. Validation split. 45 questions.
REM
REM  WHY THIS IS THE LAST THING THE ABLATION NEEDS.
REM
REM  Every ablation arm is defined as "arm A minus exactly one thing" -
REM  a property evaluation/arms.py has its own test for. So every arm is
REM  only interpretable against arm A. And the two validation campaigns
REM  ran on DIFFERENT Channel A models:
REM
REM    campaign_20260901T105355Z  A B5 G H B1 B2 B4   gpt-oss-120b
REM    campaign_20260906T221521Z  B C D E F           gpt-oss-20b
REM
REM  gpt-oss-120b reached end of life on 2026-09-03 (D46) and cannot be
REM  re-run. So the fix is to bring A onto the CURRENT binding, where
REM  the five new arms already are.
REM
REM  This is 45 questions, 135 requests upper bound (measured with
REM  --budget-only, not estimated; the arbiter fires only on
REM  disagreement so the real figure is lower). Under an hour, and the
REM  cheapest unlock left: it turns five arms that cannot be compared
REM  to anything into a five-way single-field ablation on one binding.
REM
REM  ALREADY KNOWN, so that this is not oversold: on ACCURACY the
REM  binding makes no difference at all. Arm A on 120b and arm C on 20b
REM  return the byte-identical set of 18 correct questions. What this
REM  run is actually for is the RISK SCORE - detection AUROC is a
REM  distribution, not a set, and nothing yet shows it survives the
REM  model swap unchanged.
REM
REM  It also re-measures the arbiter under the RX-045 candidate-order
REM  fix, which arms B-F could not: B has no natural channel, C has no
REM  program channel, D disables the consistency engine and the
REM  arbiter, and E disables the arbiter. The arbiter fired ONCE in all
REM  225 rows of that campaign.
REM
REM  VALIDATION ONLY. The test split is spent and run_campaign.py
REM  refuses it while every methodology-freeze tag is void (D48).
REM
REM  USAGE
REM    RUN_ARM_A_REBASE.bat check     verify setup, spend nothing
REM    RUN_ARM_A_REBASE.bat           run it
REM    RUN_ARM_A_REBASE.bat <run_id>  resume a stopped one
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
    "%PY%" scripts\run_campaign.py --arms A --split validation --budget-only
    echo.
    echo   Setup is good. Run with no arguments to start.
    echo.
    pause
    exit /b 0
)

echo.
echo   ==================================================================
echo    Arm A, validation split, 45 questions on the current binding.
echo    135 requests upper bound. Under an hour. LEAVE THIS WINDOW OPEN.
echo   ==================================================================
echo.
pause

if "%~1"=="" (
    "%PY%" scripts\run_campaign.py --arms A --split validation
) else (
    echo   resuming %~1 ...
    "%PY%" scripts\run_campaign_unattended.py --resume %~1 --arms A --max-tokens 4096
)

echo.
echo   ==================================================================
echo    Exit code %ERRORLEVEL%.  0 = finished.  3 = stopped on quota.
echo    If it stopped, re-run:  RUN_ARM_A_REBASE.bat ^<run_id^>
echo   ==================================================================
echo.
pause
