@echo off
REM ===================================================================
REM  THE MISSING FIVE ABLATION ARMS. Validation split.
REM
REM  Every campaign so far ran A, G, H plus the five baselines. So the
REM  "full A-H ablation" the spec asks for has THREE of eight arms, and
REM  H3 and H4 rest on two ablation arms out of seven. This runs the
REM  other five:
REM
REM      B   minus the natural channel
REM      C   minus the program channel
REM      D   minus the consistency engine (and therefore the arbiter)
REM      E   minus the verification agent
REM      F   minus hybrid retrieval - semantic leg only
REM
REM  D and E are the interesting ones: they are the only arms that say
REM  whether the consistency engine and the arbiter earn their place,
REM  which is what H1 is really asking.
REM
REM  VALIDATION ONLY. The test split is spent (evaluated once on
REM  2026-09-05) and run_campaign.py now refuses it outright while
REM  every methodology-freeze tag is void. These arms are therefore
REM  validation-only and the write-up must say so - they were run after
REM  the held-out set, so they cannot be held out.
REM
REM  Cost: 5 arms x 45 questions = 225 rows, 495 requests upper bound
REM  (measured by --budget-only, not estimated: B/C/D/E 90 each, F 135
REM  because semantic-only retrieval still runs both channels). The
REM  arbiter fires only on disagreement, so the real figure is lower.
REM  Roughly 2-3 hours. LEAVE THE WINDOW OPEN.
REM
REM  USAGE
REM    RUN_ABLATION.bat check       verify setup, spend nothing
REM    RUN_ABLATION.bat             run it
REM    RUN_ABLATION.bat <run_id>    resume a stopped one
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
    "%PY%" scripts\run_campaign.py --arms B C D E F --split validation --budget-only
    echo.
    echo   Setup is good. Run with no arguments to start.
    echo.
    pause
    exit /b 0
)

echo.
echo   ==================================================================
echo    Ablation arms B C D E F, validation split. 45 questions each.
echo    Roughly 2-3 hours. LEAVE THIS WINDOW OPEN.
echo.
echo    A quota stop is NORMAL, not a failure. Note the run id and
echo    re-run this file with it to continue.
echo   ==================================================================
echo.
pause

if "%~1"=="" (
    "%PY%" scripts\run_campaign.py --arms B C D E F --split validation
) else (
    echo   resuming %~1 ...
    "%PY%" scripts\run_campaign_unattended.py --resume %~1 --arms B C D E F --max-tokens 4096
)

echo.
echo   ==================================================================
echo    Exit code %ERRORLEVEL%.  0 = finished.  3 = stopped on quota.
echo    If it stopped, re-run:  RUN_ABLATION.bat ^<run_id^>
echo.
echo    When it finishes, analyse it:
echo      .venv\Scripts\python.exe scripts\analyse_campaign.py --run ^<run_id^>
echo   ==================================================================
echo.
pause
