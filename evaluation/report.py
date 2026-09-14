"""The results section, computed (spec Modules 22-25; EVALUATION.md §3-§9).

One function turns a campaign's raw rows into every table the write-up needs, so
that no number in the paper is produced by a one-off snippet nobody can re-run.
That is the whole point: a results table assembled by hand is a table whose
provenance is a memory.

Three properties are enforced here rather than left to the author's care.

**Arms that are not detectors do not appear in the detection table.** B1-B4 have
no risk score. Entering them at AUROC 0.5 would read as "this baseline detects
nothing" when the truth is "this baseline is not a detector", and a reader
comparing 0.5 against the full system's number would be comparing against a
placeholder.

**Every hypothesis verdict carries its interval and its correction.** A point
estimate favouring a hypothesis is not support. HYPOTHESES.md sets the bar at a
CI excluding the null after Holm-Bonferroni across the family, and
`hypothesis_verdicts` reports every hypothesis including the ones that fail -
EVALUATION.md §10 requires negative results to be as prominent as positive ones.

**Underpowered comparisons say so.** On 150 questions with a base error rate that
may be small, a stratified AUROC can rest on a handful of errors. Each table
carries the count it was computed from, and a stratum too thin to support a claim
is labelled rather than quietly reported to three decimal places.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from evaluation.ablation import ablation_contrasts
from evaluation.arms import ALL_ARMS
from evaluation.dataset import DatasetQuestion
from evaluation.error_analysis import ChunkTextIndex, ErrorAnalysis, analyse
from evaluation.metrics.correctness import TAU_PRIMARY
from evaluation.metrics.detection import (
    contingency,
    evaluate_detection,
    roc_auc,
    select_threshold,
)
from evaluation.metrics.efficiency import CallCost, QuestionCost, aggregate_efficiency
from evaluation.metrics.qa import AnswerRecord, evaluate_qa
from evaluation.metrics.statistics import (
    DEFAULT_RESAMPLES,
    bootstrap_ci,
    holm_bonferroni,
    paired_bootstrap_difference,
)

__all__ = [
    "MIN_STRATUM_FOR_A_CLAIM",
    "ArmResults",
    "build_report",
    "hypothesis_verdicts",
]

# Below this many errors in a stratum, an AUROC is reported with a warning
# rather than presented as a finding. Not a significance test - a floor, chosen
# so that a stratum whose interval would span most of [0,1] is labelled as such
# before anyone reads a point estimate off it.
MIN_STRATUM_FOR_A_CLAIM = 10


@dataclass
class ArmResults:
    arm: str
    qa: dict
    detection: dict | None
    stratified: dict
    contingency_table: dict
    arbitration: dict
    efficiency: dict
    errors: dict
    n: int
    notes: list[str]

    def as_dict(self) -> dict:
        return {
            "arm": self.arm,
            "n": self.n,
            "qa": self.qa,
            "detection": self.detection,
            "detection_by_provenance": self.stratified,
            "agreement_x_correctness": self.contingency_table,
            "arbitration": self.arbitration,
            "efficiency": self.efficiency,
            "error_analysis": self.errors,
            "notes": self.notes,
        }


def _answer_records(analysis: ErrorAnalysis, questions: dict[str, DatasetQuestion],
                    records_by_qid: dict[str, dict]) -> list[AnswerRecord]:
    out: list[AnswerRecord] = []
    for case in analysis.cases:
        record = records_by_qid.get(case.qid, {})
        program = record.get("program")
        out.append(
            AnswerRecord(
                question_id=case.qid,
                prediction=_reparse(record),
                gold=questions[case.qid].gold_value(),
                abstained=bool(record.get("abstained")),
                program_executed=(
                    None if not isinstance(program, dict) else bool(program.get("available"))
                ),
                ambiguous=case.ambiguous,
                stratum=case.stratum,
            )
        )
    return out


def _reparse(record: dict):
    from backend.core.financial_value import parse_financial_value

    text = record.get("answer_text") or record.get("answer")
    return parse_financial_value(text) if text else None


def _efficiency(records: list[dict]) -> dict:
    """Efficiency from what the artifact actually recorded.

    Token counts are only present when the provider reported them. Rows without
    usage produce a zero here, which would understate the arm's cost, so the
    coverage is reported alongside rather than the total being presented as
    complete.
    """
    costs: list[QuestionCost] = []
    with_usage = 0
    unaccounted = 0
    for record in records:
        calls: list[CallCost] = []
        # natural and program carry one call each; B5 carries `samples`, one
        # usage block per self-consistency draw. Counting a 5-sample arm as one
        # call understates it fivefold, which is exactly the quantity D11's
        # "at matched API cost" comparison exists to check.
        payloads: list[tuple[str, dict]] = []
        for channel in ("natural", "program"):
            payload = record.get(channel)
            if isinstance(payload, dict):
                payloads.append((channel, payload))
        for sample in record.get("samples") or ():
            if isinstance(sample, dict):
                payloads.append(("natural_sample", sample))

        for channel, payload in payloads:
            usage = payload.get("usage")
            if not isinstance(usage, dict):
                # Artifacts written before 2026-09 used a flat `tokens` key.
                if not payload.get("tokens"):
                    continue
                usage = {"total_tokens": payload["tokens"]}
            total = int(usage.get("total_tokens") or 0)
            if not total:
                continue
            with_usage += 1
            calls.append(
                CallCost(
                    channel=channel,
                    # Not recorded per call. Left empty rather than guessed from
                    # config: an arm that rebinds a channel mid-run would be
                    # attributed to the wrong provider, and no provider has a
                    # published rate recorded anyway, so nothing downstream
                    # loses a figure it could otherwise have had.
                    provider=payload.get("provider", ""),
                    model=payload.get("model", ""),
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    reasoning_tokens=int(usage.get("reasoning_tokens") or 0),
                    total_tokens=total,
                    latency_seconds=float(usage.get("latency_seconds") or 0.0),
                    cost_usd=float(usage.get("cost_usd") or 0.0),
                    equivalent_cost_usd=float(usage.get("equivalent_cost_usd") or 0.0),
                    cached=bool(usage.get("cached")),
                )
            )

        # `tokens_used` is the row's own total. Where it exceeds what the
        # per-channel blocks account for, tokens were spent by a call that
        # recorded no usage - the verification agent is the known case, and it
        # would otherwise vanish from every cost figure silently.
        recorded = int(record.get("tokens_used") or 0)
        counted = sum(call.total_tokens for call in calls)
        if recorded > counted:
            unaccounted += recorded - counted
        costs.append(
            QuestionCost(
                question_id=record.get("question_id", ""),
                calls=tuple(calls),
                wall_clock_seconds=float(record.get("latency_seconds") or 0.0),
                verification_agent_triggered=bool(
                    (record.get("verification") or {}).get("triggered")
                ),
            )
        )
    report = aggregate_efficiency(costs).as_dict()
    report["rows_with_token_usage"] = with_usage
    report["unattributed_tokens"] = unaccounted
    notes = list(report.get("notes", ()))
    if not with_usage:
        notes.append(
            "no row in this arm recorded token usage, so tokens_per_question "
            "is 0 by absence rather than by measurement"
        )
    if unaccounted:
        notes.append(
            f"{unaccounted:,} tokens appear in rows' own `tokens_used` totals but "
            "in no per-channel usage block, so they are excluded from "
            "tokens_by_channel. The verification agent is the known case: it is "
            "invoked without its usage being recorded, so an arm whose arbiter "
            "fires is undercounted here by that much"
        )
    if notes:
        report["notes"] = notes
    return report


def _canonical_matches(value, gold_canonical, tau: Decimal) -> bool:
    """Scale-resolved comparison, which is the only fair one here.

    Channel records carry both `value` (as printed) and `canonical` (scale and
    currency resolved). Comparing the printed form against gold is the RX-031
    trap in miniature: a channel that answers `73338` where gold says
    `INR 73338 crore` is RIGHT, and a bare-numeral comparison scores it wrong.
    Measured before this landed, that mistake reported "no channel ever had the
    correct answer" across every arbitration in the project - 0 of 16, when the
    true figure is 7.
    """
    if value is None or gold_canonical is None:
        return False
    try:
        got, want = Decimal(str(value)), Decimal(str(gold_canonical))
    except (ArithmeticError, ValueError):
        return False
    if want == 0:
        return got == 0
    return abs(got - want) / abs(want) <= tau


def _arbitration(records: list[dict], cases, questions: dict[str, DatasetQuestion],
                 *, tau: Decimal) -> dict:
    """EVALUATION.md §5.4: what the arbiter did when it was triggered.

    The last unmeasured item on Module 13's acceptance list, and it needed no new
    data - it was computable from the run artifacts all along, because every row
    records whether the arbiter fired, whether it resolved, and what the final
    answer was.

    Resolution accuracy is only defined where the arbiter RESOLVED. An arbiter
    that declines is doing the thing D19 designed it to do - it sees two answers
    and no indication of which channel produced which, so declining is a valid
    outcome, and counting it as a wrong resolution would punish the abstention
    this project treats as first-class everywhere else.

    **`correct_was_available` is the figure that makes the rest readable.** The
    arbiter fires on disagreement, and on most of those questions BOTH channels
    are wrong - so a low resolution accuracy mostly measures how hard those
    questions are, not how well the arbiter chooses. Only where a channel
    actually held the right answer was there a choice to get right, and that is
    the denominator a claim about the arbiter belongs over.

    `rate` is None below `MIN_STRATUM_FOR_A_CLAIM`. The arbiter fires rarely, and
    a "resolution accuracy" of 1.000 on two questions is a number that will be
    quoted without its denominator.
    """
    correct_by_qid = {c.qid: c.correct for c in cases}
    triggered = [
        r for r in records if (r.get("verification") or {}).get("triggered")
    ]
    resolved = [r for r in triggered if (r.get("verification") or {}).get("resolved")]
    graded = [r for r in resolved if r.get("question_id") in correct_by_qid]
    right = sum(1 for r in graded if correct_by_qid[r["question_id"]])

    winnable = 0
    won = 0
    for record in graded:
        question = questions.get(record["question_id"])
        gold = question.gold_value() if question is not None else None
        gold_canonical = gold.canonical() if gold is not None else None
        available = any(
            _canonical_matches((record.get(channel) or {}).get("canonical"),
                               gold_canonical, tau)
            for channel in ("natural", "program")
        )
        if available:
            winnable += 1
            won += bool(correct_by_qid[record["question_id"]])

    report = {
        "triggered": len(triggered),
        "trigger_rate": round(len(triggered) / len(records), 4) if records else None,
        "resolved": len(resolved),
        "declined": len(triggered) - len(resolved),
        "graded": len(graded),
        "correct": right,
        "correct_was_available": winnable,
        "correct_when_available": won,
        "rate": None,
        "note": None,
    }
    if not triggered:
        report["note"] = (
            "the arbiter never fired, so resolution accuracy is undefined rather "
            "than 0 - there is nothing it got wrong"
        )
    elif len(graded) < MIN_STRATUM_FOR_A_CLAIM:
        report["note"] = (
            f"{right} of {len(graded)} resolved correctly. On {winnable} of those "
            f"a channel actually held the right answer, and the arbiter returned "
            f"it {won} time(s) - that is the pair worth reading, because on the "
            f"rest neither channel had anything correct to choose. Below the "
            f"{MIN_STRATUM_FOR_A_CLAIM}-case floor, so the ratio is withheld: it "
            "would be quoted without its denominator"
        )
    else:
        report["rate"] = round(right / len(graded), 4)
    return report


def _detection(cases, *, threshold: float | None, resamples: int) -> dict | None:
    scored = [c for c in cases if c.risk_score is not None]
    if not scored:
        return None
    scores = [c.risk_score for c in scored]
    labels = [0 if c.correct else 1 for c in scored]
    report = evaluate_detection(scores, labels, threshold=threshold).as_dict()

    # The interval, not just the point. On this dataset size an AUROC gap of
    # 0.05 sits comfortably inside the noise.
    interval = bootstrap_ci(
        list(zip(scores, labels, strict=True)),
        lambda pairs: roc_auc([s for s, _ in pairs], [y for _, y in pairs]),
        resamples=resamples,
    )
    report["auroc_ci"] = interval.as_dict()
    if report["positives"] < MIN_STRATUM_FOR_A_CLAIM:
        report["notes"] = list(report["notes"]) + [
            (
                f"only {report['positives']} incorrect answer(s): too few to "
                "support a claim about detection quality, and the interval will "
                "be very wide"
            )
        ]
    return report


def _stratified(cases, *, threshold: float | None, resamples: int) -> dict:
    """H2's split. Reported separately AND pooled, never pooled alone."""
    out: dict[str, dict | None] = {}
    for name in ("retrieval_caused", "reasoning_caused", "unknown"):
        subset = [c for c in cases if c.stratum == name]
        out[name] = _detection(subset, threshold=threshold, resamples=resamples)
    out["pooled"] = _detection(cases, threshold=threshold, resamples=resamples)
    out["note"] = (
        "Both channels read the same evidence, so agreement is expected to be "
        "near-blind to retrieval-caused error. The pooled figure averages across "
        "a regime where the method cannot work and would overstate the "
        "contribution if reported alone (EVALUATION.md §5.3)."
    )
    return out


def build_report(
    records: list[dict],
    questions: dict[str, DatasetQuestion],
    *,
    arms: list[str] | None = None,
    threshold: float | None = None,
    tau: Decimal = TAU_PRIMARY,
    resamples: int = DEFAULT_RESAMPLES,
    chunk_index: ChunkTextIndex | None = None,
) -> dict:
    """Every table, from one campaign's rows.

    `threshold` is the operating point fixed on validation and frozen. Passing
    None omits the precision/recall row entirely rather than inventing a
    default: a precision figure at an unrecorded threshold is not reproducible,
    and EVALUATION.md §5.2 forbids selecting it on the data being reported.
    """
    index = chunk_index or ChunkTextIndex()
    present = arms or sorted({r.get("arm") for r in records if r.get("arm")})
    results: dict[str, ArmResults] = {}
    # Graded once per arm and shared with the hypothesis tests. Re-deriving them
    # there would re-parse every answer and re-check every evidence span, and
    # would let the two halves of the report disagree if either changed.
    cases_by_arm: dict[str, list] = {}

    for arm in present:
        arm_records = [r for r in records if r.get("arm") == arm and not r.get("error")]
        failed = sum(1 for r in records if r.get("arm") == arm and r.get("error"))
        analysis = analyse(arm_records, questions, arm=arm, index=index, tau=tau)
        cases_by_arm[arm] = analysis.cases
        by_qid = {r.get("question_id"): r for r in arm_records}

        config = ALL_ARMS.get(arm)
        notes = list(analysis.notes)
        if failed:
            notes.append(
                f"{failed} question(s) failed with an error and are excluded from "
                "the metrics; they remain in the run artifact"
            )

        detection: dict | None
        if config is not None and not config.provides_detection_score:
            detection = None
            notes.append(
                f"{arm} produces no risk score, so it is a QA baseline only. It is "
                "absent from the detection table rather than entered at AUROC 0.5, "
                "which would read as a measurement"
            )
        else:
            detection = _detection(analysis.cases, threshold=threshold, resamples=resamples)

        graded = [c for c in analysis.cases if c.agreed is not None]
        results[arm] = ArmResults(
            arm=arm,
            n=len(analysis.cases),
            qa=evaluate_qa(_answer_records(analysis, questions, by_qid), tau=tau).as_dict(),
            detection=detection,
            stratified=(
                _stratified(analysis.cases, threshold=threshold, resamples=resamples)
                if detection is not None
                else {}
            ),
            contingency_table=(
                contingency([bool(c.agreed) for c in graded], [c.correct for c in graded])
                if graded
                else {}
            ),
            arbitration=_arbitration(arm_records, analysis.cases, questions, tau=tau),
            efficiency=_efficiency(arm_records),
            errors=analysis.as_dict(threshold=threshold if threshold is not None else 0.5),
            notes=notes,
        )

    return {
        "arms": {name: r.as_dict() for name, r in results.items()},
        "questions_in_gold": len(questions),
        "operating_threshold": threshold,
        "tau": str(tau),
        "resamples": resamples,
        "hypotheses": hypothesis_verdicts(cases_by_arm, results, resamples=resamples),
        # Separate from `hypotheses` on purpose. H1-H5 are the pre-registered
        # predictions; these are the spec's Module 24 ablation, which asks a
        # different question - what each component contributes - against a
        # different comparator. Filing them together would let an ablation
        # contrast be reported as a hypothesis test that was never registered.
        "ablation": ablation_contrasts(cases_by_arm, resamples=resamples),
    }


def _scores_and_labels(cases_by_arm: dict[str, list], arm: str):
    return [
        (c.qid, c.risk_score, 0 if c.correct else 1)
        for c in cases_by_arm.get(arm, ())
        if c.risk_score is not None
    ]


def _paired(left: list[tuple], right: list[tuple]) -> list[tuple]:
    """Questions both arms answered, aligned. The pairing HYPOTHESES.md requires.

    Comparing arms on different question sets would let a difference in question
    difficulty masquerade as a difference in method.
    """
    right_by_qid = {qid: (score, label) for qid, score, label in right}
    out = []
    for qid, score, label in left:
        if qid in right_by_qid:
            out.append((score, label, *right_by_qid[qid]))
    return out


def hypothesis_verdicts(
    cases_by_arm: dict[str, list],
    results: dict[str, ArmResults],
    *,
    resamples: int = DEFAULT_RESAMPLES,
) -> dict:
    """H1-H5, each with its interval, and the family-wise correction.

    Every hypothesis appears whether or not it was supported, and a hypothesis
    that could not be tested says so rather than being omitted - an absent row
    reads as an oversight, and "not testable on this data" is itself a result.
    """
    verdicts: dict[str, dict] = {}
    p_values: dict[str, float] = {}

    def auroc_difference(name: str, arm_a: str, arm_b: str, rationale: str) -> None:
        left = _scores_and_labels(cases_by_arm, arm_a)
        right = _scores_and_labels(cases_by_arm, arm_b)
        paired = _paired(left, right)
        if len(paired) < MIN_STRATUM_FOR_A_CLAIM:
            verdicts[name] = {
                "testable": False,
                "reason": (
                    f"only {len(paired)} question(s) were answered by both {arm_a} "
                    f"and {arm_b}; not enough to test this"
                ),
                "rationale": rationale,
            }
            return
        interval, p = paired_bootstrap_difference(
            paired,
            lambda rows: roc_auc([r[0] for r in rows], [r[1] for r in rows]),
            lambda rows: roc_auc([r[2] for r in rows], [r[3] for r in rows]),
            resamples=resamples,
        )
        p_values[name] = p
        verdicts[name] = {
            "testable": True,
            "comparison": f"AUROC({arm_a}) - AUROC({arm_b})",
            "rationale": rationale,
            "interval": interval.as_dict(),
            "p_value": p,
            "supported_before_correction": bool(
                interval.excludes_zero and (interval.point or 0) > 0
            ),
        }

    auroc_difference(
        "H1", "A", "B5",
        "cross-modality disagreement vs same-model self-consistency, at matched cost",
    )
    auroc_difference(
        "H4", "A", "H",
        "cross-model channels vs the same model on both channels - the direct test of D1",
    )
    auroc_difference("H3", "A", "G", "with vs without the deterministic verifier")

    # H2 is a within-arm comparison of two strata, not a between-arm one.
    cases = cases_by_arm.get("A", [])
    strata = {
        name: [c for c in cases if c.stratum == name and c.risk_score is not None]
        for name in ("reasoning_caused", "retrieval_caused")
    }
    if min(len(v) for v in strata.values()) < MIN_STRATUM_FOR_A_CLAIM:
        verdicts["H2"] = {
            "testable": False,
            "reason": (
                "one stratum has too few questions: "
                + ", ".join(f"{k}={len(v)}" for k, v in strata.items())
            ),
            "rationale": "detection should be far better on reasoning-caused errors",
        }
    else:
        reasoning = strata["reasoning_caused"]
        retrieval = strata["retrieval_caused"]
        a = roc_auc([c.risk_score for c in reasoning], [0 if c.correct else 1 for c in reasoning])
        b = roc_auc([c.risk_score for c in retrieval], [0 if c.correct else 1 for c in retrieval])
        undefined = [
            f"{name} ({len(rows)} question(s), "
            f"{sum(1 for c in rows if not c.correct)} error(s))"
            for name, rows, auc in (
                ("reasoning_caused", reasoning, a),
                ("retrieval_caused", retrieval, b),
            )
            if auc is None
        ]
        verdicts["H2"] = {
            "testable": a is not None and b is not None,
            "reasoning_caused_auroc": a,
            "retrieval_caused_auroc": b,
            "difference": None if (a is None or b is None) else a - b,
            # A stratum can clear the size gate and still have no AUROC: the
            # measure is undefined without both an error and a non-error. Saying
            # only "not testable" hid the reason, and here the reason is the
            # result - a stratum with no errors in it is the system never having
            # failed on a question whose evidence it actually had.
            **(
                {
                    "reason": (
                        "AUROC is undefined for "
                        + " and ".join(undefined)
                        + ": it needs at least one error and one correct answer "
                        "in the stratum"
                    )
                }
                if undefined
                else {}
            ),
            "rationale": (
                "the mechanism check on H1. A positive H1 with a failed H2 must be "
                "reported as an unexplained correlation, not a validated design"
            ),
            "note": (
                "the two strata are different question sets, so this comparison is "
                "UNPAIRED and its interval is correspondingly wider than the "
                "between-arm comparisons above"
            ),
        }

    # H5 is about the cost curve, not about detection quality alone.
    full = results.get("A")
    if full is not None:
        curve = []
        for name, result in results.items():
            if result.detection and result.detection.get("auroc") is not None:
                curve.append(
                    {
                        "arm": name,
                        "auroc": result.detection["auroc"],
                        "tokens_per_question": result.efficiency.get("tokens_per_question"),
                        "calls_per_question": result.efficiency.get("calls_per_question"),
                    }
                )
        verdicts["H5"] = {
            "testable": len(curve) >= 2,
            "curve": sorted(curve, key=lambda r: -(r["auroc"] or 0)),
            "rationale": (
                "at least one reduced configuration should reach 80% of the full "
                "detection gain at 50% of the cost"
            ),
            "note": (
                "cost here is tokens and calls, which were measured. A monetary "
                "ratio is omitted because no provider carries an established paid "
                "rate - see efficiency.py"
            ),
        }

    return {
        "verdicts": verdicts,
        "family_wise_correction": holm_bonferroni(p_values) if p_values else {},
        "note": (
            "Holm-Bonferroni across the tested family. Five hypotheses at alpha "
            "0.05 each carries roughly a one-in-four chance of a spurious "
            "'supported'. Untestable hypotheses are excluded from the correction "
            "and reported as untestable."
        ),
    }


def choose_operating_threshold(
    records: list[dict],
    questions: dict[str, DatasetQuestion],
    *,
    arm: str = "A",
    min_recall: float | None = None,
) -> float:
    """Fix the operating point on VALIDATION data, to be frozen before test.

    Separated from `build_report` so that the split it was chosen on is a
    decision the caller makes explicitly and records, rather than something that
    happens implicitly inside a reporting function.
    """
    cases = [
        c
        for c in analyse(
            [r for r in records if r.get("arm") == arm and not r.get("error")],
            questions,
            arm=arm,
        ).cases
        if c.risk_score is not None
    ]
    if not cases:
        raise ValueError(f"arm {arm} produced no risk scores; no threshold can be chosen")
    return select_threshold(
        [c.risk_score for c in cases],
        [0 if c.correct else 1 for c in cases],
        min_recall=min_recall,
    )
