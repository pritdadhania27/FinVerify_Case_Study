# Deployment

Spec §39. Verified on this machine: all four services built, started and
answered. Every claim below was checked by running it.

---

## The two tiers, and why they are separate files

```
Frontend (nginx)          docker-compose.app.yml
     │  /api → api:8000
FastAPI (uvicorn)         docker-compose.app.yml
     │
     ├── PostgreSQL       docker-compose.yml
     ├── Qdrant           docker-compose.yml
     └── Agent engine     runs on the HOST, not as a service
             ├── LLM providers   (free-tier HTTP APIs)
             ├── Sandbox         (a fresh container per execution)
             └── Verifier
```

**`docker-compose.yml` is the research stack**: PostgreSQL and Qdrant, and
nothing else. A campaign runs from the host — it needs the venv, the corpus and
the run artifacts — so `docker compose up -d` brings up exactly what an
experiment depends on. Building a web image is not something an evaluation run
should have to wait for.

**`docker-compose.app.yml` adds the application tier**: the API and the UI. It
is additive, so the research stack is unaffected by whether the app tier is up.

**The agent engine is deliberately not a service.** Generated code runs in a
fresh, disposable container created per execution by
`backend/services/sandbox.py`. Isolation is therefore per-run rather than
per-deployment: a long-lived sandbox service would accumulate state between
executions, and a container that has run other people's generated code is not a
clean room.

---

## Running it

```bash
# Research only — what a campaign needs
docker compose up -d
docker compose ps

# Full stack, including the API and UI.
# BUILD_REF is a BUILD ARG, baked into the image - so it needs `--build`, and
# `--force-recreate` alone will not pick up a new value. Without it /health
# reports build_ref "unknown" and verify_deployed_stack.py fails that check,
# which is correct behaviour: the field exists to catch a container serving
# code from a fortnight ago, and guessing a ref would defeat it.
$env:BUILD_REF = git rev-parse --short HEAD
docker compose -f docker-compose.yml -f docker-compose.app.yml up -d --build

# Schema (runs from the host, against whichever database DATABASE_URL names)
.venv/Scripts/python.exe -m alembic upgrade head

# Load artifacts into the queryable projection
.venv/Scripts/python.exe scripts/ingest_to_database.py --all

# Verify the DEPLOYED stack, not the source tree: 27 read-only checks over the
# running containers - every endpoint answers, the image reports the
# build_ref it was built from, the frontend is served and proxies the API. Exits non-zero on any
# failure. Compare that build_ref with git rev-parse --short HEAD: that comparison is what catches a green-looking stack serving stale
# code, so run it after every deploy.
.venv/Scripts/python.exe scripts/verify_deployed_stack.py
```

| Service | Host port | Purpose |
|---|---|---|
| `finverify-postgres` | `5433` | the queryable projection (see below) |
| `finverify-qdrant` | `6333` / `6334` | chunk vectors |
| `finverify-api` | `8000` | FastAPI |
| `finverify-frontend` | `5173` | nginx serving the built UI, proxying `/api` |

### PostgreSQL is on 5433, not 5432

Not a preference — a diagnosis. On this machine **two processes were listening
on 5432**: the container and a native Windows PostgreSQL service. Every
connection from the host authenticated against the native one, which has no
`finverify` role, and the only symptom was `password authentication failed`
while `docker compose ps` reported the container healthy. `netstat -ano | grep
5432` showed both listeners.

`POSTGRES_PORT` was already parameterised in the Compose file, so the project's
container moved to 5433 and the user's system service was left alone. The
lesson generalises: **a healthy container is not a reachable service.** The
API's `/health` endpoint reports reachability by making the call for exactly
this reason.

---

## Configuration

All configuration is environment variables. `.env` is gitignored; `.env.example`
documents every key with a safe local default.

| Variable | Used by | Note |
|---|---|---|
| `DATABASE_URL` | API, Alembic, ingest | No default anywhere in source |
| `QDRANT_URL` | API, indexing | `http://qdrant:6333` inside Compose |
| `QDRANT_COLLECTION` | API, retrieval | `finverify_e5` |
| `GROQ_API_KEY`, `GEMINI_API_KEY` | provider layer | free tier |
| `*_CHANNEL_MODEL` | provider layer | `provider/model` in one variable |
| `FINVERIFY_ENABLE_LIVE_QA` | API | **off unless `1`** — see below |
| `API_UPSTREAM` | frontend (nginx) | where `/api/` is proxied; default `api:8000`, `host.docker.internal:8001` for live QA |
| `FINVERIFY_ALLOW_TEST` | dataset layer | the sealed split |
| `TESSERACT_CMD` | OCR | optional override |

### No connection string in source

`alembic.ini` ships with `sqlalchemy.url` **empty** and `env.py` reads
`DATABASE_URL` from `.env`. `backend/database/session.py` raises rather than
falling back to a default URL. A connection string in a committed file is a
credential in the repository even when it is only a local one — and that is
precisely the kind that survives into a deployment, because it happens to work.

### Live question answering is off by default

`POST /questions/ask` runs both reasoning channels and can trigger the arbiter.
Every request spends free-tier quota that the evaluation campaign depends on
(D14), and the quota — not money — is this project's binding constraint. An open
endpoint is how a day of campaign budget goes to a crawler or a refresh loop.

The endpoint returns **503 with the reason**, not a stub answer, and refuses
before constructing a provider.

**Enabling it on the API container does not work, and this section used to say it
did.** The container is web-only (see the images below): it has no LangGraph,
embedding model, LLM client or Docker access, so with the flag set every question
died on an import error as a bare 500. Since D51 it returns 503 naming the missing
engine. Live QA runs from the project environment instead, with the dashboard
pointed at it:

```bat
RUN_LIVE_DEMO.bat
```

which is equivalent to:

```powershell
docker compose up -d
$env:API_UPSTREAM = "host.docker.internal:8001"
docker compose -f docker-compose.yml -f docker-compose.app.yml up -d --build frontend
$env:FINVERIFY_ENABLE_LIVE_QA = "1"
.venv\Scripts\python.exe -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8001 --env-file .env
```

The live API binds 127.0.0.1. Docker Desktop's `host.docker.internal` still reaches
it from the dashboard container, so the endpoint that spends quota is not exposed
on the network. The embedding model loads in a background thread at startup, so
the first question does not pay for it. To return to the read-only deployment,
recreate the frontend without `API_UPSTREAM`.

Live answers are stored in the database under run `live-qa` so they can be
reopened, and excluded from `/stats` and the research views. Nothing is written to
`experiments/runs/` (D51).

---

## The images

**`docker/api.Dockerfile`** installs only what the API imports — not
`requirements.lock.txt`, which pins the full research environment including
torch and camelot. None of that is touched by a service that serves rows from
Postgres, and a web image that takes ten minutes to build is a web image nobody
rebuilds.

Making that true required a real fix rather than a smaller pin. `QdrantIndex`
imported `Chunk` from `chunking`, which imports `extraction`, which imported
`pymupdf` and `camelot` at module scope — so counting vectors required a PDF
table parser. `Chunk` is used purely as a type annotation, so it moved under
`TYPE_CHECKING`, and camelot's import moved into the function that uses it.

The symptom is worth recording because it was misleading: `/health` reported
`vector_index: false`, i.e. **a missing dependency presented as an unreachable
service**. The health check now carries the exception in its `note`, so the next
failure of this shape names itself.

Runs as a non-root user (uid 10001). The API accepts uploads, and an upload
handler running as root is one path-traversal bug away from a much larger
problem.

**`docker/frontend.Dockerfile`** is a two-stage build: `node:24-alpine` runs
`npm ci && npm run build`, and `nginx:1.27-alpine` serves `dist/`. nginx proxies
`/api/` to the API container, so the browser makes same-origin requests and the
backend needs no CORS configuration — which matters, because a permissive CORS
policy on a service that can spend free-tier quota is a way to lose it.
`proxy_read_timeout` is 900 s: a live question measured 167 s normally and 691 s
when the program channel's provider timed out on read, and the frontend's own deadline (920 s) is kept above it. A
question that outlasts the proxy still finishes and is saved under `live-qa`; the
page says so rather than reporting the API as down.

---

## The sandbox

Not in Compose, by design. `backend/services/sandbox.py` creates a container per
execution with:

- no network,
- a read-only filesystem,
- a non-root uid,
- a memory cap and a CPU quota,
- a wall-clock timeout with a kill and cleanup.

AST allowlist validation runs **before** anything is executed, and there is no
host-execution fallback: if Docker is unavailable, program execution is
`BLOCKED` and reported as such. A verification channel that silently ran
untrusted generated code on the host would be worse than no verification
channel.

54 sandbox-escape attempts are in the test suite. They execute real containers
and are **skipped, not passed**, when Docker is down, so a green run on a
machine without Docker never reads as verified containment.

---

## Reproducing the environment

```bash
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.lock.txt
.venv/Scripts/python.exe scripts/verify_environment.py      # must exit 0
.venv/Scripts/python.exe scripts/verify_llm_providers.py    # real API calls
```

`requirements.lock.txt` is a full `pip freeze` (122 packages). `pyproject.toml`
records intent; the lock file is the reproducible truth. It now includes
`uvicorn` and `python-multipart`, so the lock file can actually start the
service it documents.

---

## What is not deployed

Stated because a deployment document that lists only what exists is misleading.

- **No TLS, no authentication, no rate limiting.** This runs on localhost for a
  research project. Exposing it to a network needs all three, and the `users`
  table exists as a place to put an identity rather than because anything uses
  one.
- **No CI pipeline.** Tests and lint run locally.
- **No production secret management.** `.env` on disk is the mechanism.
- **No horizontal scaling.** One API container, one of each service. The
  binding constraint is free-tier request quota, which more containers would
  consume faster rather than serve better.

---

*Environment evidence: [`ENVIRONMENT.md`](ENVIRONMENT.md) · Current state:
[`PROJECT_STATUS.md`](PROJECT_STATUS.md) · Decisions:
[`DECISIONS.md`](DECISIONS.md)*
