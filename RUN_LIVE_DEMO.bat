@echo off
REM FinVerify-AI - the full demo, with live question answering switched ON.
REM
REM Why the API runs on the host here: the API container is deliberately
REM web-only (no torch, LangGraph, LLM client or Docker access), so it cannot run
REM the pipeline. Live questions are served by the same API code running from
REM .venv, which has the research engine, and the dashboard container is pointed
REM at it. The read-only container API keeps running but is not used. See D51.
REM
REM Each question spends free-tier LLM quota and takes a few minutes.
REM To return to the default read-only deployment afterwards:
REM   docker compose -f docker-compose.yml -f docker-compose.app.yml up -d frontend

cd /d "%~dp0"

echo [1/3] Database and vector index
docker compose up -d || goto :fail

echo [2/3] Dashboard, proxied to the live API on this machine
set "API_UPSTREAM=host.docker.internal:8001"
for /f %%i in ('git rev-parse --short HEAD') do set "BUILD_REF=%%i"
docker compose -f docker-compose.yml -f docker-compose.app.yml up -d --build frontend || goto :fail

echo [3/3] Live API on http://127.0.0.1:8001  - Ctrl+C stops it
echo       Dashboard: http://localhost:5173   (the embedding model loads in the background first)
set "FINVERIFY_ENABLE_LIVE_QA=1"
set "FINVERIFY_BUILD_REF=%BUILD_REF%"
.venv\Scripts\python.exe -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8001 --env-file .env
goto :eof

:fail
echo Startup failed - is Docker Desktop running?
exit /b 1
