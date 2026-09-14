"""The real-world case study (spec Module 27).

Spec §35 asks for a representative set of Indian companies and public reports,
run end to end, analysed on accuracy, disagreement rate, hallucination
detection, error types, difficult question categories, cost, latency and
verification effectiveness.

**This module computes the case study; it does not narrate one.** Every figure
comes from `build_report` and the run artifacts, so the write-up cannot contain
a number nobody can re-derive. `render_markdown` produces prose *around*
computed values rather than prose *containing* claims - a distinction that
matters because a case study is exactly where a plausible sentence slips past
the evidence.

**It refuses to produce a case study from nothing.** With no graded questions it
returns a document that says so and stops. A template with zeroes in it reads,
at a glance, like a system that scored zero rather than a campaign that has not
run - and the difference is the whole content of the page.

**Per-company breakdowns are reported with their cell sizes.** The corpus is
five companies across five sectors, and a per-company accuracy computed over
eleven questions is not a finding about that sector. `MIN_CELL` marks cells too
thin to carry a claim rather than letting a reader take a three-question cell at
face value.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from statistics import median

from evaluation.dataset import DatasetQuestion
from evaluation.error_analysis import AnalysedCase

__all__ = ["MIN_CELL", "CompanyBreakdown", "CaseStudy", "build_case_study", "render_markdown"]

# Below this many questions, a per-cell rate is reported with a warning rather
# than as a result. Not a significance test - a floor, past which a percentage
# is mostly an artefact of which questions happened to be written.
MIN_CELL = 15


@dataclass(frozen=True)
class CompanyBreakdown:
    company: str
    sector: str | None
    questions: int
    correct: int
    disagreements: int
    both_agree_wrong: int

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.questions if self.questions else None

    @property
    def disagreement_rate(self) -> float | None:
        return self.disagreements / self.questions if self.questions else None

    @property
    def thin(self) -> bool:
        return self.questions < MIN_CELL

    def as_dict(self) -> dict:
        return {
            "company": self.company,
            "sector": self.sector,
            "questions": self.questions,
            "accuracy": self.accuracy,
            "disagreement_rate": self.disagreement_rate,
            "both_agree_wrong": self.both_agree_wrong,
            "too_thin_for_a_claim": self.thin,
        }


@dataclass
class CaseStudy:
    arm: str
    questions: int = 0
    correct: int = 0
    abstained: int = 0
    disagreements: int = 0
    # verdict == DISAGREE only. `disagreements` above counts `agreed is False`,
    # which also includes UNCERTAIN (a channel gave no figure).
    numeric_disagreements: int = 0
    both_agree_wrong: int = 0
    companies: list[CompanyBreakdown] = field(default_factory=list)
    by_question_type: dict[str, dict] = field(default_factory=dict)
    error_kinds: dict[str, int] = field(default_factory=dict)
    error_provenance: dict[str, int] = field(default_factory=dict)
    latency_p50: float | None = None
    latency_p95: float | None = None
    tokens_per_question: float | None = None
    verification_trigger_rate: float | None = None
    verification_resolved: int = 0
    verification_abstained: int = 0
    detection: dict | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def has_data(self) -> bool:
        return self.questions > 0

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.questions if self.questions else None

    @property
    def disagreement_rate(self) -> float | None:
        return self.disagreements / self.questions if self.questions else None

    def hardest_question_types(self) -> list[tuple[str, dict]]:
        """Question types ordered worst-accuracy first, cell size attached."""
        return sorted(
            self.by_question_type.items(),
            key=lambda kv: (kv[1]["accuracy"] if kv[1]["accuracy"] is not None else 1.0),
        )

    def as_dict(self) -> dict:
        return {
            "arm": self.arm,
            "questions": self.questions,
            "accuracy": self.accuracy,
            "abstention_rate": self.abstained / self.questions if self.questions else None,
            "disagreement_rate": self.disagreement_rate,
            "both_agree_wrong": self.both_agree_wrong,
            "companies": [c.as_dict() for c in self.companies],
            "by_question_type": self.by_question_type,
            "error_kinds": self.error_kinds,
            "error_provenance": self.error_provenance,
            "latency_p50_seconds": self.latency_p50,
            "latency_p95_seconds": self.latency_p95,
            "tokens_per_question": self.tokens_per_question,
            "verification": {
                "trigger_rate": self.verification_trigger_rate,
                "resolved": self.verification_resolved,
                "abstained": self.verification_abstained,
            },
            "detection": self.detection,
            "notes": self.notes,
        }


def _row_tokens(record: dict) -> int:
    """Total tokens a recorded row spent, or 0 if it genuinely recorded none.

    This reader exists because the previous one read `record[channel]["tokens"]`,
    a key the recorder stopped writing in 2026-09 - so every row of every modern
    campaign looked like it had spent nothing, and the case study printed "no row
    recorded token usage" over artifacts where all 61 rows recorded it. That is
    worse than a wrong number: it is a generated document asserting data is
    absent when the data is there.

    The same defect was found and fixed in `evaluation/metrics/efficiency.py`
    and never propagated here, which is the real lesson - one wrong key, two
    readers, one fixed.

    `tokens_used` is preferred over summing the channels because it is the row's
    OWN total and so includes calls that record no per-channel usage block. The
    verification agent is the known case: summing `natural` and `program` silently
    undercounts any arm whose arbiter fired. `evaluation/report.py` is the
    canonical reader and documents the same precedence; this is the scalar
    version of it, not a second opinion.
    """
    recorded = int(record.get("tokens_used") or 0)
    if recorded:
        return recorded
    total = 0
    for channel in ("natural", "program"):
        payload = record.get(channel)
        if not isinstance(payload, dict):
            continue
        usage = payload.get("usage")
        if isinstance(usage, dict):
            total += int(usage.get("total_tokens") or 0)
        else:
            # Artifacts written before 2026-09 used a flat `tokens` key. Kept so
            # the early runs in experiments/runs/ stay readable - they are
            # append-only and committed, so this path cannot be retired.
            total += int(payload.get("tokens") or 0)
    return total


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] * (1 - (position - lower)) + ordered[upper] * (position - lower)


def build_case_study(
    cases: list[AnalysedCase],
    questions: dict[str, DatasetQuestion],
    records: list[dict],
    *,
    arm: str = "A",
    sectors: dict[str, str] | None = None,
    detection: dict | None = None,
) -> CaseStudy:
    """Assemble spec §35's analysis from graded cases and their run rows."""
    study = CaseStudy(arm=arm, detection=detection)
    if not cases:
        study.notes.append(
            "No graded questions. The campaign has not run, or no question in it "
            "has a validated gold answer yet."
        )
        return study

    by_qid = {r.get("question_id"): r for r in records if r.get("arm") == arm}
    sectors = sectors or {}

    study.questions = len(cases)
    study.correct = sum(1 for c in cases if c.correct)
    study.disagreements = sum(1 for c in cases if c.agreed is False)
    study.numeric_disagreements = sum(
        1 for c in cases if (by_qid.get(c.qid) or {}).get("verdict") == "DISAGREE"
    )
    study.both_agree_wrong = sum(1 for c in cases if c.both_agree_wrong)
    study.abstained = sum(
        1 for c in cases if (by_qid.get(c.qid) or {}).get("abstained")
    )

    # --- per company -------------------------------------------------------
    grouped: dict[str, list[AnalysedCase]] = defaultdict(list)
    for case in cases:
        question = questions.get(case.qid)
        grouped[question.company if question else "unknown"].append(case)
    for company, group in sorted(grouped.items()):
        study.companies.append(
            CompanyBreakdown(
                company=company,
                sector=sectors.get(company),
                questions=len(group),
                correct=sum(1 for c in group if c.correct),
                disagreements=sum(1 for c in group if c.agreed is False),
                both_agree_wrong=sum(1 for c in group if c.both_agree_wrong),
            )
        )
    thin = [c.company for c in study.companies if c.thin]
    if thin:
        study.notes.append(
            f"{len(thin)} company cell(s) hold fewer than {MIN_CELL} questions "
            f"({', '.join(thin)}); their rates are reported but must not be read "
            "as findings about those sectors"
        )

    # --- by question type: which categories are hard -----------------------
    by_type: dict[str, list[AnalysedCase]] = defaultdict(list)
    for case in cases:
        question = questions.get(case.qid)
        by_type[question.question_type if question else "unknown"].append(case)
    for kind, group in sorted(by_type.items()):
        correct = sum(1 for c in group if c.correct)
        study.by_question_type[kind] = {
            "questions": len(group),
            "correct": correct,
            "accuracy": correct / len(group) if group else None,
            "disagreement_rate": (
                sum(1 for c in group if c.agreed is False) / len(group) if group else None
            ),
            "too_thin_for_a_claim": len(group) < MIN_CELL,
        }

    # --- error taxonomy, both axes ----------------------------------------
    errors = [c for c in cases if not c.correct and c.label is not None]
    study.error_kinds = dict(Counter(c.label.kind.value for c in errors))
    study.error_provenance = dict(Counter(c.label.provenance.value for c in errors))
    needs_human = sum(1 for c in errors if c.label.confidence.value == "needs_human")
    if needs_human:
        study.notes.append(
            f"{needs_human} of {len(errors)} error label(s) are SUGGESTED or "
            "need a human: comparing two numbers cannot separate a wrong metric "
            "from a wrong formula from an arithmetic slip"
        )

    # --- cost and latency --------------------------------------------------
    rows = [by_qid.get(c.qid) or {} for c in cases]
    latencies = [float(r["latency_seconds"]) for r in rows if r.get("latency_seconds")]
    study.latency_p50 = median(latencies) if latencies else None
    study.latency_p95 = _percentile(latencies, 0.95)

    tokens = [_row_tokens(r) for r in rows]
    measured = [t for t in tokens if t]
    study.tokens_per_question = (sum(measured) / len(measured)) if measured else None
    if not measured:
        study.notes.append(
            "no row recorded token usage, so cost per question is absent rather "
            "than zero"
        )

    # --- verification effectiveness ---------------------------------------
    triggered = [r for r in rows if (r.get("verification") or {}).get("triggered")]
    study.verification_trigger_rate = len(triggered) / len(rows) if rows else None
    study.verification_resolved = sum(
        1 for r in triggered if (r.get("verification") or {}).get("resolved")
    )
    study.verification_abstained = len(triggered) - study.verification_resolved
    return study


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _num(value: float | None, places: int = 2) -> str:
    return "—" if value is None else f"{value:.{places}f}"


def render_markdown(
    study: CaseStudy, *, title: str = "Case study", corpus: str = ""
) -> str:
    """Render the study. Prose around computed values, never prose containing claims.

    `corpus` describes the companies and reports selected. It is rendered even
    when there are no results, because selecting a representative set IS part of
    spec §35 and that part is finished - dropping it would make completed work
    look absent.
    """
    if not study.has_data:
        return (
            f"# {title}\n\n"
            + (corpus + "\n\n" if corpus else "")
            + "## Results\n\n"
            "**Not yet produced.** " + " ".join(study.notes) + "\n\n"
            "This section deliberately contains no table. A template filled with "
            "zeroes reads, at a glance, like a system that scored zero rather "
            "than a campaign that has not run, and the difference is the whole "
            "content of the page.\n\n"
            "To produce it: validate FinVerify-IND gold answers "
            "(`scripts/build_finverify_ind.py export` -> validate -> `import`), "
            "run `scripts/run_campaign.py`, then "
            "`scripts/analyse_campaign.py --case-study`.\n"
        )

    lines = [f"# {title}", ""]
    if corpus:
        lines.extend([corpus, ""])
    lines.append(
        f"Arm **{study.arm}** over **{study.questions}** graded questions from "
        f"{len(study.companies)} companies."
    )
    lines.append("")
    lines.append("## Headline")
    lines.append("")
    if study.questions < MIN_CELL:
        # The per-company table has marked thin cells since this module was
        # written, but the headline did not - so a run of one question rendered
        # "Numerical accuracy 100.0%" with no qualifier at the top of the page,
        # above a table that correctly called the same cell too thin to read.
        # The headline is the line that gets quoted, so it carries the warning
        # first rather than deferring it to the caveats at the bottom.
        lines.append(
            f"> **{study.questions} graded question"
            f"{'' if study.questions == 1 else 's'}. Every rate in this table is "
            f"too thin to be a finding.** The threshold is {MIN_CELL}. A "
            f"percentage over {study.questions} row"
            f"{'' if study.questions == 1 else 's'} moves in steps of "
            f"{100 / study.questions:.0f} points, so it describes which questions "
            f"happened to run, not how the system performs."
        )
        lines.append("")
    lines.append("| | |")
    lines.append("|---|---|")
    lines.append(f"| Numerical accuracy | {_pct(study.accuracy)} |")
    # Two rows, because they measure different things and the single row that
    # used to stand here conflated them. `agreed` is `verdict == AGREE`, so "did
    # not agree" pools a real disagreement with a channel producing nothing. On
    # the held-out arm A that row read 68.9% while the channels disagreed on a
    # figure in 3 of 61 questions (EVALUATION.md 5.4).
    lines.append(
        f"| Channels did not agree (disagreed, or one gave no figure) | "
        f"{_pct(study.disagreement_rate)} |"
    )
    lines.append(
        f"| Channels disagreed on a figure (verdict DISAGREE) | "
        f"{study.numeric_disagreements} of {study.questions} "
        f"({_pct(study.numeric_disagreements / study.questions)}) |"
    )
    lines.append(f"| Abstention rate | {_pct(study.abstained / study.questions)} |")
    lines.append(
        f"| **Both channels agreed and both were wrong** | "
        f"{study.both_agree_wrong} of {study.questions} |"
    )
    lines.append(f"| Latency p50 / p95 | {_num(study.latency_p50)}s / {_num(study.latency_p95)}s |")
    lines.append(f"| Tokens per question | {_num(study.tokens_per_question, 0)} |")
    lines.append("")
    lines.append(
        "The both-agree-wrong row is the method's blind spot and is placed in "
        "the headline rather than an appendix: it counts the questions where the "
        "detector was confident and wrong, which is the number a reader deciding "
        "whether to trust this system actually needs."
    )
    lines.append("")

    lines.append("## By company")
    lines.append("")
    lines.append("| Company | Sector | n | Accuracy | Did not agree | Both wrong | |")
    lines.append("|---|---|---:|---:|---:|---:|---|")
    for company in study.companies:
        flag = "**thin cell**" if company.thin else ""
        lines.append(
            f"| {company.company} | {company.sector or '—'} | {company.questions} | "
            f"{_pct(company.accuracy)} | {_pct(company.disagreement_rate)} | "
            f"{company.both_agree_wrong} | {flag} |"
        )
    lines.append("")
    lines.append(
        f"A cell below {MIN_CELL} questions is marked. A per-sector accuracy "
        "computed over a handful of questions is an artefact of which questions "
        "happened to be written, not a finding about the sector."
    )
    lines.append("")

    lines.append("## Which question categories are hard")
    lines.append("")
    lines.append("| Type | n | Accuracy | Did not agree | |")
    lines.append("|---|---:|---:|---:|---|")
    for kind, stats in study.hardest_question_types():
        flag = "**thin cell**" if stats["too_thin_for_a_claim"] else ""
        lines.append(
            f"| {kind} | {stats['questions']} | {_pct(stats['accuracy'])} | "
            f"{_pct(stats['disagreement_rate'])} | {flag} |"
        )
    lines.append("")

    if study.error_provenance or study.error_kinds:
        lines.append("## Error taxonomy")
        lines.append("")
        lines.append(
            "Two axes, because they answer different questions: where the error "
            "entered the pipeline, and what it looks like. One label cannot serve "
            "both (D25)."
        )
        lines.append("")
        lines.append("| Provenance | n | | Kind | n |")
        lines.append("|---|---:|---|---|---:|")
        provenance = sorted(study.error_provenance.items(), key=lambda kv: -kv[1])
        kinds = sorted(study.error_kinds.items(), key=lambda kv: -kv[1])
        for index in range(max(len(provenance), len(kinds))):
            left = f"{provenance[index][0]} | {provenance[index][1]}" if index < len(
                provenance
            ) else " | "
            right = f"{kinds[index][0]} | {kinds[index][1]}" if index < len(kinds) else " | "
            lines.append(f"| {left} | | {right} |")
        lines.append("")

    lines.append("## Verification effectiveness")
    lines.append("")
    lines.append(
        f"The arbiter was triggered on {_pct(study.verification_trigger_rate)} of "
        f"questions. It resolved {study.verification_resolved} and abstained on "
        f"{study.verification_abstained}."
    )
    lines.append("")
    lines.append(
        "An abstention is not a failure. When the evidence does not settle a "
        "disagreement, declining to resolve it leaves the answer flagged as "
        "risky, which is the safe outcome; a resolution manufactured from "
        "inadequate evidence would remove the flag without removing the risk."
    )
    lines.append("")

    if study.detection:
        lines.append("## Detection")
        lines.append("")
        auroc = study.detection.get("auroc")
        ci = study.detection.get("auroc_ci") or {}
        lines.append(
            f"AUROC {_num(auroc, 3)}"
            + (
                f" (95% CI {_num(ci.get('ci_low'), 3)}–{_num(ci.get('ci_high'), 3)})"
                if ci.get("ci_low") is not None
                else ""
            )
            + f", over {study.detection.get('positives', 0)} incorrect answers."
        )
        for note in study.detection.get("notes", ()):
            lines.append(f"- {note}")
        lines.append("")

    if study.notes:
        lines.append("## Caveats")
        lines.append("")
        lines.extend(f"- {note}" for note in study.notes)
        lines.append("")

    lines.append(
        "Absolute figures here are free-tier-model figures and must never be set "
        "beside published frontier-model results as though the setups matched."
    )
    lines.append("")
    return "\n".join(lines)
