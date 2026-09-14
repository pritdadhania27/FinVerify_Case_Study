"""The frontend's contract with the backend, checked without a browser.

Spec §43 asks every module for unit tests, and Module 21 had none of any kind:
no test runner in `frontend/package.json`, no `*.test.*` anywhere under
`frontend/src`. The gap was recorded honestly in TESTING.md but the module was
still marked COMPLETE.

Rather than add a second toolchain for a nine-file UI, these tests cover the
class of frontend bug that static typing cannot and a live probe catches only by
luck: the **seams between the two tiers**. TypeScript checks that `api.ts` is
internally consistent; nothing checked that the paths it calls still exist in
`main.py`, or that every route resolves to a page that exists. Both are silent
at build time and loud at runtime, which is the worst combination.

This is deliberately not a substitute for rendering the pages - that is what the
scripted live probe does, and D-level reasoning for the split is recorded in
DECISIONS.md. It is the part that can be checked deterministically, in CI, with
no Docker and no node.

Every defect these tests would have caught has actually happened in this
project: `Ask.tsx` hard-coded seven arms against the fourteen the API serves, and
a page called `api.evidence()` with a 404 swallowed into an empty state.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend" / "src"
API_MAIN = ROOT / "backend" / "api" / "main.py"


def _api_ts() -> str:
    return (FRONTEND / "lib" / "api.ts").read_text(encoding="utf-8")


def _main_tsx() -> str:
    return (FRONTEND / "main.tsx").read_text(encoding="utf-8")


def _backend_routes() -> set[str]:
    """Every path the FastAPI app declares, as a template string."""
    source = API_MAIN.read_text(encoding="utf-8")
    return set(re.findall(r'@app\.(?:get|post|put|delete|patch)\(\s*"([^"]+)"', source))


def _frontend_paths() -> set[str]:
    """Every API path `api.ts` builds, reduced to its leading static segment.

    A call is written as a template literal with interpolated ids and query
    strings (`/answers/${id}`), so the comparison is made on the first segment -
    enough to catch an endpoint that was renamed or removed, which is the defect
    this guards.
    """
    source = _api_ts()
    found: set[str] = set()
    # Both quoting styles api.ts uses, anchored on the `request(` helper's arg
    # and on direct fetch paths.
    for match in re.findall(r"[`'\"](/[a-zA-Z][\w\-/]*)", source):
        if match == "/api":  # the proxy prefix, not an endpoint
            continue
        first = "/" + match.lstrip("/").split("/")[0]
        found.add(first)
    return found


def test_every_endpoint_the_frontend_calls_exists_in_the_backend():
    """Catches a renamed or deleted endpoint, which typing cannot see.

    `api.ts` holds plain strings; the compiler is perfectly happy with a path
    that 404s. The failure then surfaces as an error box on a screen, blamed on
    the service being down.
    """
    backend_first_segments = {"/" + p.lstrip("/").split("/")[0] for p in _backend_routes()}
    missing = sorted(_frontend_paths() - backend_first_segments)
    assert not missing, (
        f"the frontend calls {missing}, which the API does not declare. "
        f"API declares: {sorted(backend_first_segments)}"
    )


def test_the_api_helper_never_hardcodes_a_host():
    """Every call must go through the nginx proxy prefix.

    A hardcoded `http://localhost:8000` works on the developer's machine and
    fails in the container, where the browser is on the host and the API is on a
    compose network. It also breaks the moment the stack is served from anywhere
    but localhost.
    """
    offenders = []
    for path in sorted(FRONTEND.rglob("*.ts*")):
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), 1):
            if re.search(r"https?://(localhost|127\.0\.0\.1)", line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert not offenders, "hardcoded host in the frontend:\n  " + "\n  ".join(offenders)


def test_every_route_resolves_to_a_page_that_exists():
    """A route pointing at a deleted component is a blank screen, not an error."""
    source = _main_tsx()
    imported = dict(
        re.findall(
            # A default import, optionally followed by named imports:
            # `import NotFound, { RouteError } from './pages/NotFound'`.
            r"import\s+(\w+)\s*(?:,\s*\{[^}]*\})?\s+from\s+'\./(pages/[\w/]+)'",
            source,
        )
    )
    elements = re.findall(r"element:\s*<(\w+)\s*/>", source)
    unknown = sorted({e for e in elements if e not in imported and e != "App"})
    assert not unknown, f"routes reference components with no import: {unknown}"

    for name, module in imported.items():
        assert (FRONTEND / f"{module}.tsx").exists(), (
            f"{name} is imported from {module}, which does not exist"
        )


def test_no_page_module_is_orphaned():
    """An unrouted screen is dead code that still typechecks and still builds.

    Module 21's acceptance claim is a screen count, so a page nothing can reach
    would inflate it. `App.tsx` is the shell, not a screen.
    """
    source = _main_tsx()
    routed = set(re.findall(r"from\s+'\./pages/(\w+)'", source))
    on_disk = {p.stem for p in (FRONTEND / "pages").glob("*.tsx")}
    orphans = sorted(on_disk - routed)
    assert not orphans, f"these pages exist but no route reaches them: {orphans}"


def test_the_screen_count_the_status_table_claims_matches_the_routes():
    """PROJECT_STATUS states a number of screens; it must come from the router.

    A count maintained by hand in a status table is a count that drifts - this
    one was '6 screens' for two days after the seventh and eighth were added.
    """
    source = _main_tsx()
    # Every child route that renders a page, excluding the catch-all.
    routes = re.findall(r"\{\s*(?:index:\s*true|path:\s*'([^']+)')\s*,\s*element:", source)
    # '/' is the App shell that every screen renders inside, and '*' is the
    # catch-all; neither is a screen. `index: true` captures empty and IS one -
    # the dashboard.
    screens = [r for r in routes if r not in ("*", "/")]
    assert len(screens) == 8, (
        f"the router defines {len(screens)} screens (excluding the catch-all), "
        "but PROJECT_STATUS module 21 claims 8"
    )


def test_arms_are_not_hardcoded_in_any_screen():
    """The arm list must come from the API, not from a literal in the UI.

    `Ask.tsx` hard-coded seven arms while `/arms` served fourteen, so arms B-F -
    the single-field ablation the whole research design rests on - could not be
    selected on the only screen that runs anything. A literal arm array is the
    shape of that bug.
    """
    offenders = []
    # An array of three or more short quoted arm-like tokens, e.g. ['A','B','C'].
    pattern = re.compile(r"\[\s*(?:'(?:[A-H]|B[1-5]|O|P)'\s*,\s*){2,}")
    for path in sorted((FRONTEND / "pages").glob("*.tsx")):
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:90]}")
    assert not offenders, (
        "an arm list is hardcoded in a screen; serve it from /arms instead:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize(
    "marker",
    [
        "undefined-value",  # an unmeasurable metric must not render as 0
        "UNSCORED",  # no detector ran, which is not a risk band
    ],
)
def test_the_states_that_must_not_read_as_zero_have_a_style(marker):
    """These two are the project's central UI invariant, so they need a rule.

    An UNDEFINED metric rendered as 0.000 says "worse than chance" where the
    truth is "this stratum could not test it". If the class is dropped from the
    stylesheet the markup still renders - as ordinary body text, silently losing
    the distinction.
    """
    css = (FRONTEND / "index.css").read_text(encoding="utf-8")
    assert marker in css, f"{marker!r} is used in the UI but has no style rule"


def test_every_api_call_has_a_deadline():
    """A request with no deadline hangs its screen when a dependency stalls.

    With PostgreSQL stopped, the API held requests open and every screen stayed on
    "Loading" with no error. The live question needs a far longer deadline than a
    read, and must not inherit the read deadline.
    """
    source = _api_ts()
    assert "AbortController" in source and "signal" in source
    ask_call = source.split("ask:")[1].split("upload:")[0]
    assert "LIVE_QUESTION_TIMEOUT_MS" in ask_call


def test_the_research_run_picker_offers_runs_by_metrics_not_answers():
    """Research shows metrics, so its picker filters on the metric count.

    It listed every registered run with an answer count, so 26 development runs
    appeared as "(0 answers)" and led to an empty page. Filtering on answers
    instead would have hidden the pooled ablation report, which has no answers of
    its own.
    """
    source = (FRONTEND / "pages" / "Research.tsx").read_text(encoding="utf-8")
    assert "run.results > 0" in source
    assert "(run) => run.answers > 0" not in source


def test_a_live_question_outlasts_the_proxy_and_a_timeout_says_it_is_still_running():
    """The client deadline must exceed nginx's, and the ask timeout must not say "down".

    A live question took 691 s under load. nginx gave up at 600 s, the page said the
    API was not responding, and the API went on to finish and save the answer.
    """
    nginx = (ROOT / "docker" / "nginx.conf").read_text(encoding="utf-8")
    proxy_seconds = int(re.search(r"proxy_read_timeout (\d+)s;", nginx).group(1))
    source = (FRONTEND / "lib" / "api.ts").read_text(encoding="utf-8")
    client_ms = int(
        re.search(r"LIVE_QUESTION_TIMEOUT_MS = ([\d_]+)", source).group(1).replace("_", "")
    )
    assert client_ms > proxy_seconds * 1000
    assert "response.status === 504 && path === '/questions/ask'" in source
    assert "saves the answer" in source


def test_the_detection_chart_names_the_split_it_was_measured_on():
    """A validation run's chart must not be captioned as a held-out result.

    The caption was the literal "Held-out detection AUROC" for every run, so the
    pooled ablation - validation only, permanently - read as held out.
    """
    source = (FRONTEND / "pages" / "Research.tsx").read_text(encoding="utf-8")
    assert 'caption="Held-out detection AUROC' not in source
    assert "selectedRun?.split === 'test'" in source
