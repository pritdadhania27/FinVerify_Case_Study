# FinVerify-AI frontend

React + TypeScript + Vite. Six screens, per spec §29:
Dashboard · Documents · Document viewer · Financial QA · Verification · Research.

**The UI is secondary to the research engine**, and that is not a disclaimer —
it is the design constraint. No number is computed here. Every figure on screen
is served by the API, which serves a projection of the run artifacts, which are
the source of truth on disk (decision D4). If a table here disagrees with a
committed artifact, the artifact is right.

## Running it

```bash
# 1. the API (from the repo root)
.venv/Scripts/python.exe -m uvicorn backend.api.main:app --port 8000

# 2. the UI
cd frontend
npm install
npm run dev            # http://localhost:5173
```

Vite proxies `/api` to `127.0.0.1:8000`, so no base URL is baked into the build
and the backend needs no CORS configuration for a deployment that is only ever
local.

```bash
npm run typecheck      # tsc --noEmit, strict
npm run build          # typecheck + production bundle into dist/
```

## Three things the UI must not do

Each of these would misrepresent the system rather than merely look wrong.

1. **Show a bare number.** A figure is displayed as `answer_text` — `INR 3956
   crore` — never as the bare magnitude. `3956` beside a gold of `3,956 crore`
   looks like a match and is a hundredfold error, which is precisely the class
   of mistake this project exists to detect.
2. **Invent a risk score.** Baselines B1–B4 have no detector. Their score is
   `null` and renders as *not scored*, never as a neutral-looking 0.5 that a
   reader would take for a measurement. The same rule applies to an undefined
   metric on the research screen: it renders as **undefined**, not 0.000, because
   an AUROC on a stratum with no errors was not measurable rather than bad.
3. **Present the score as a probability.** It is uncalibrated. It ranks answers
   by relative risk, and the caveat is displayed beside it rather than left in
   the documentation.

## Notes

- Hash routing, because this builds to static files with no server rewrite
  rules and browser routing would 404 on a refresh of any deep link.
- The document viewer shows provenance rather than rendering the PDF. These are
  third-party annual reports and the repository does not redistribute them; the
  SHA-256 is what lets a reader confirm the file they fetch from the publisher
  is the file the evaluation used.
- Asking a question is disabled unless the API is started with
  `FINVERIFY_ENABLE_LIVE_QA=1`. Every request runs both reasoning channels and
  can trigger the arbiter, spending free-tier quota the evaluation campaign
  depends on. The dashboard says so rather than letting the 503 read as a bug.
- No UI framework and no state library. Six screens over a read-mostly API do
  not need either, and each would be a dependency to keep current for a
  component of the project the specification calls secondary.
