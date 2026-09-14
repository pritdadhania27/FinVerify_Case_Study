"""End-to-end checks against the RUNNING stack, not the test doubles.

Spec §19's integration checklist, asked of the deployed system: does the thing
on the real PostgreSQL and the real Qdrant, serving from the container it was
built into, actually do these. The unit suite covers each with an in-memory
database and stubbed channels, and that is a different question.

    .\\.venv\\Scripts\\python.exe scripts\\verify_deployed_stack.py

**It earns its place.** On its first run it failed two checks - `GET
/answers/{id}` returned `channels: []` and `evidence: []` for every answer, with
the rows sitting in `reasoning_runs`, `program_runs` and `evidence` all along.
Thirty-seven API tests passed at the same moment, because an empty list is a
valid value for a field no test asserts on. That is the shape of nearly every
defect of consequence on this project: the suite is green and the system is
wrong, and only running it says so.

Requires the app tier to be up:

    docker compose -f docker-compose.yml -f docker-compose.app.yml up -d --build

Read-only. It makes no LLM calls and spends no quota - `POST /questions/ask` is
probed with a GET precisely so that it cannot answer anything.

Prints PASS/FAIL per item and exits non-zero if anything failed.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

API = "http://localhost:8000"
UI = "http://localhost:5173"

failures: list[str] = []


def get(url: str):
    request = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            return response.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(name)


print("\nDEPLOYED STACK (spec section 19 integration checklist)\n")

status, body = get(f"{API}/health")
health = json.loads(body) if status == 200 else {}
check("API responds", status == 200, f"HTTP {status}")
check("database reachable", health.get("database") is True)
check("vector index usable (preflight, not just reachable)", health.get("vector_index") is True)
check("build_ref reported", bool(health.get("build_ref")) and health["build_ref"] != "unknown",
      health.get("build_ref", ""))

status, body = get(f"{API}/stats")
stats = json.loads(body) if status == 200 else {}
check("corpus statistics served", status == 200)
check("documents ingested", stats.get("documents", 0) > 0, f"{stats.get('documents')} filings")
check("pages persisted", stats.get("pages", 0) > 1000, f"{stats.get('pages')} pages")
check("tables extracted", stats.get("tables", 0) > 0, f"{stats.get('tables')} tables")
check("gold answers persisted", stats.get("questions_validated", 0) > 0,
      f"{stats.get('questions_validated')} validated of {stats.get('questions_total')}")
check("validation status broken out, not totalled",
      stats.get("questions_validated", 0)
      + stats.get("questions_rejected", 0)
      + stats.get("questions_pending", 0) == stats.get("questions_total"))

status, body = get(f"{API}/documents")
documents = json.loads(body) if status == 200 else []
check("documents listed", status == 200 and len(documents) > 0)
if documents:
    first = documents[0]
    check("provenance travels with the document",
          all(first.get(k) for k in ("sha256", "source_url", "retrieved_on", "company")),
          f"{first.get('company')} / {first.get('retrieved_on')}")
    status, _ = get(f"{API}/documents/{first['document_id']}")
    check("single document retrievable", status == 200)

status, body = get(f"{API}/experiments")
experiments = json.loads(body) if status == 200 else []
check("experiment recording readable", status == 200 and len(experiments) > 0,
      f"{len(experiments)} runs")
with_answers = [e for e in experiments if e["answers"] > 0]
check("at least one run carries answers", bool(with_answers),
      ", ".join(f"{e['run_id']}={e['answers']}" for e in with_answers))
# Campaign runs only. The live-qa run (D51) records "live demonstration - not an
# experiment" as its independence, and counting it let this check pass on a
# demonstration while proving nothing about how any campaign's channels were bound.
campaigns = [e for e in with_answers if e.get("split") != "live"]
check("channel independence recorded per run",
      any(e.get("independence") for e in campaigns),
      next((e["independence"] for e in campaigns if e.get("independence")), "")[:60])

status, body = get(f"{API}/answers?limit=200")
answers = json.loads(body) if status == 200 else []
check("answers listable without knowing an id", status == 200 and len(answers) > 0)
# A recorded campaign answer, not merely the newest row. Live questions (D51) are
# stored too, newest first, and have no gold evidence by construction - so probing
# answers[0] failed two gold-evidence checks against an API that was correct. The
# fallback keeps a genuine gap loud: with no campaign answer, the checks still run.
row = next((a for a in answers if a.get("run_id") != "live-qa"), answers[0] if answers else None)
if row:
    check("answer carries its question text", bool(row.get("question")))
    status, body = get(f"{API}/answers/{row['answer_id']}")
    detail = json.loads(body) if status == 200 else {}
    check("answer detail retrievable", status == 200)
    check("evidence attached to the answer", len(detail.get("evidence") or []) > 0,
          f"{len(detail.get('evidence') or [])} spans")
    check("both channels recorded", len(detail.get("channels") or []) > 0,
          ", ".join(c["name"] for c in (detail.get("channels") or [])))
    status, _ = get(f"{API}/verification/{row['answer_id']}")
    check("verification record retrievable", status == 200)
    status, body = get(f"{API}/evidence/{row['question_id']}")
    spans = json.loads(body) if status == 200 else []
    check("gold evidence served by question id", status == 200 and len(spans) > 0,
          f"page {spans[0].get('page')}" if spans else "")

status, body = get(f"{API}/questions/ask")
check("live QA is gated, not open", status in (405, 503, 422),
      f"HTTP {status} on GET (endpoint exists and is not silently answering)")

status, _ = get(f"{API}/answers/99999999")
check("a missing answer is 404, not a stub", status == 404, f"HTTP {status}")

status, body = get(f"{UI}/")
check("frontend served", status == 200)
status, body = get(f"{UI}/api/stats")
check("frontend proxies the API", status == 200)

print()
if failures:
    print(f"{len(failures)} FAILED: {', '.join(failures)}")
    sys.exit(1)
print("all checks passed against the deployed stack")
