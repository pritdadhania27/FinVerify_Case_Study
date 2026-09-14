@echo off
REM ===================================================================
REM  Review the gold rows whose evidence anchors went stale, and commit
REM  the verdicts back into the dataset.
REM
REM  Nothing here is typed, so there is no backtick, no line
REM  continuation and no quoting to get wrong. Every previous attempt
REM  at this by hand failed on one of those.
REM
REM  USAGE
REM    REVIEW_GOLD.bat            the TWO that need a person
REM    REVIEW_GOLD.bat all        all 11 flagged rows
REM    REVIEW_GOLD.bat import     commit verdicts into the dataset
REM
REM  Background: the validator corrected these answers, and the import
REM  left the anchors citing the figure that was rejected (RX-042). The
REM  ANSWER is not what needs checking - the anchors are. Two rows have
REM  a further problem: their recorded answer appears nowhere in the
REM  filing. See TODO 0b.
REM ===================================================================

cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
set "SHEET=datasets\finverify_ind\worksheet.csv"

if not exist "%PY%" (
    echo.
    echo   ERROR: no venv interpreter at %PY%
    echo.
    pause
    exit /b 1
)

set "TWO=FI82a95e99 FIce2f3063"
set "ALL=FI5d61cd35 FI82a95e99 FI8fdff60d FI939a55f7 FI995ccc77 FI684732e4 FI6d4f1b3c FI78abdcdf FI7e56d1c6 FI85d75350 FIce2f3063"

if /i "%~1"=="import" goto doimport

set "QIDS=%TWO%"
if /i "%~1"=="all" set "QIDS=%ALL%"

echo.
echo   ==================================================================
echo    Reviewing: %QIDS%
echo.
echo    The ANSWER is already yours - it is the ANCHORS that are stale.
echo    For the two below, the recorded answer is in NO chunk of the
echo    filing, so it needs the PDF:
echo.
echo      FI82a95e99  Reliance, other financial liabilities, FY2023-24
echo                  recorded 61,269 - appears on 0 pages
echo                  YOUR NOTE says 27,493, which IS on p67 and p84
echo                  other candidates: p98 25,068/41,202
echo                                    p139 52,381/68,849
echo.
echo      FIce2f3063  Tata Motors, trade payables, YE 2023-03-31
echo                  recorded 79,214 - appears on 0 pages
echo                  rejected 6,944.85 is on p282, a cash-flow row
echo.
echo    Press c to enter a corrected figure, and put the reasoning in
echo    the note. Quit any time with q - answers are saved.
echo   ==================================================================
echo.
pause

"%PY%" scripts\review_gold.py --qid %QIDS%

echo.
echo   Verdicts are saved in %SHEET%.
echo   When you are done, run:  REVIEW_GOLD.bat import
echo.
pause
exit /b 0

:doimport
echo.
echo   ==================================================================
echo    This WRITES to datasets\finverify_ind\finverify_ind_v1.json.
echo    It applies every verdict in the worksheet, not only the rows you
echo    just looked at, and any corrected answer has its stale anchors
echo    dropped. The file is git-tracked, so `git checkout` undoes it.
echo   ==================================================================
echo.
set "OK="
set /p OK=  Type yes to import:
if /i not "%OK%"=="yes" (
    echo.
    echo   Nothing written.
    echo.
    pause
    exit /b 0
)

echo.
set "WHO=%~2"
if "%WHO%"=="" set /p WHO=  Your name (recorded as the validator):
if "%WHO%"=="" (
    echo.
    echo   A validator name is required - an unattributed import is refused,
    echo   because a gold label with no author cannot be audited.
    echo.
    pause
    exit /b 1
)

echo.
echo   importing %SHEET% as "%WHO%" ...
echo.
"%PY%" scripts\build_finverify_ind.py import --csv "%SHEET%" --validator "%WHO%"
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo   Imported. If any answer was corrected, its stale anchors were
    echo   dropped and named above - they now need re-deriving before
    echo   anything reads evidence for those questions again.
) else (
    echo   Import exited %RC% - nothing was written if it refused.
)
echo.
pause
exit /b %RC%
