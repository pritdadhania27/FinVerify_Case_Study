"""Turn a campaign's raw rows into the results section (spec Modules 22-25).

    python scripts/analyse_campaign.py --run campaign_20260826T120000Z
    python scripts/analyse_campaign.py --run <id> --choose-threshold-on <validation-run-id>

**The operating threshold is chosen on validation and then frozen.** This script
will not select it from the run it is reporting: EVALUATION.md §5.2 is explicit
that doing so inflates precision and recall by construction, because the
threshold would be fitted to the data it is then scored on. Either pass
`--threshold` (a value already frozen) or `--choose-threshold-on <run_id>`
naming a *different*, validation-split run. With neither, the precision/recall
row is omitted rather than filled with a default nobody recorded.

Output goes to `evaluation/reports/` as JSON, plus a readable summary on stdout.
Every number in the write-up should be traceable to a file here.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._console import use_utf8  # noqa: E402
from backend.core.paths import project_path  # noqa: E402

use_utf8()

from evaluation.case_study import build_case_study, render_markdown
from evaluation.dataset import DATASET_ROOT, load_dataset
from evaluation.error_analysis import analyse
from evaluation.report import build_report, choose_operating_threshold
from experiments.campaign import RUNS_ROOT, load_records

# Sector labels for the case study's per-company table (spec 35 asks for a
# representative set of Indian companies). Not derived from the filings: sector
# classification is a judgement, so it is written down here where it can be
# argued with rather than inferred somewhere and presented as data.
SECTORS = {
    "Infosys Limited": "IT services",
    "HDFC Bank Limited": "Banking",
    "Reliance Industries Limited": "Energy / conglomerate",
    "Sun Pharmaceutical Industries Limited": "Pharmaceuticals",
    "Tata Motors Limited": "Automotive",
}

DATASET_PATH = DATASET_ROOT / "finverify_ind_v1.json"
REPORTS = project_path("evaluation/reports")


def _fmt(value, places: int = 3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{places}f}"
    return str(value)


def summarise(report: dict) -> None:
    print("\n" + "=" * 78)
    print("QA")
    print(f"{'arm':<6}{'n':>5}{'accuracy':>11}{'exact':>9}{'abstain':>9}{'parse-fail':>12}")
    for name, arm in sorted(report["arms"].items()):
        qa = arm["qa"]
        print(
            f"{name:<6}{qa['n']:>5}{_fmt(qa['numerical_accuracy']):>11}"
            f"{_fmt(qa['exact_match']):>9}{_fmt(qa['abstention_rate']):>9}"
            f"{_fmt(qa['parse_failure_rate']):>12}"
        )

    print("\nDETECTION (arms without a risk score are absent by design)")
    print(f"{'arm':<6}{'n':>5}{'errors':>8}{'AUROC':>9}{'95% CI':>20}{'AUPRC':>9}")
    for name, arm in sorted(report["arms"].items()):
        detection = arm["detection"]
        if detection is None:
            continue
        ci = detection.get("auroc_ci") or {}
        interval = (
            f"[{_fmt(ci.get('ci_low'))}, {_fmt(ci.get('ci_high'))}]"
            if ci.get("ci_low") is not None
            else "n/a"
        )
        print(
            f"{name:<6}{detection['n']:>5}{detection['positives']:>8}"
            f"{_fmt(detection['auroc']):>9}{interval:>20}{_fmt(detection['auprc']):>9}"
        )

    print("\nH2 STRATIFICATION - the mechanism check")
    for name, arm in sorted(report["arms"].items()):
        strata = arm.get("detection_by_provenance") or {}
        if not strata:
            continue
        row = []
        for key in ("reasoning_caused", "retrieval_caused", "pooled"):
            block = strata.get(key)
            row.append(f"{key}={_fmt(block['auroc']) if block else 'n/a'}")
        print(f"  {name}: " + "  ".join(row))

    print("\nBLIND SPOT - both channels agreed and both were wrong")
    for name, arm in sorted(report["arms"].items()):
        blind = (arm.get("error_analysis") or {}).get("blind_spot") or {}
        if blind.get("of_agreed"):
            print(
                f"  {name}: {blind['both_agree_wrong']} of {blind['of_agreed']} agreed "
                f"answers were wrong (rate {_fmt(blind['rate_among_agreed'])})"
            )

    # EVALUATION.md 5.4. Printed with `winnable` beside it because the raw
    # ratio is misleading on its own: the arbiter fires on disagreement, and on
    # most of those questions neither channel has a correct answer to choose.
    arbitrated = [
        (name, arm["arbitration"]) for name, arm in sorted(report["arms"].items())
        if (arm.get("arbitration") or {}).get("triggered")
    ]
    if arbitrated:
        print("\nARBITER - resolution accuracy where it fired (EVALUATION.md 5.4)")
        for name, arb in arbitrated:
            print(
                f"  {name}: fired {arb['triggered']} ({_fmt(arb['trigger_rate'])}), "
                f"resolved {arb['resolved']}, declined {arb['declined']}, "
                f"correct {arb['correct']}  |  a channel HAD it on "
                f"{arb['correct_was_available']}, returned on "
                f"{arb['correct_when_available']}"
            )

    # Spec Module 24. Printed with the error count beside every figure: the
    # committed-only family pairs ~20 questions carrying 3 errors, and a
    # p-value of 0.0000 on 3 errors is a statement about a bootstrap, not about
    # two methods.
    ablation = report.get("ablation") or {}
    if ablation.get("contrasts"):
        print(f"\nABLATION - each arm is {ablation['baseline']} minus one field (Module 24)")
        for name, block in sorted(ablation["contrasts"].items()):
            for family in ("all", "committed"):
                measured = block.get(family) or {}
                if not measured.get("testable"):
                    print(f"  {name:6} {family:10} NOT TESTABLE - {measured.get('reason', '')}")
                    continue
                flag = "  UNDERPOWERED" if measured.get("underpowered") else ""
                print(
                    f"  {name:6} {family:10} {_fmt(measured['point']):>7} "
                    f"[{_fmt(measured['ci_low'])}, {_fmt(measured['ci_high'])}]  "
                    f"p={_fmt(measured['p_value'], 4)}  n={measured['n']}  "
                    f"errors={measured['errors']}{flag}"
                )
        for family, rows in sorted((ablation.get("family_wise_correction") or {}).items()):
            for name, row in sorted(rows.items()):
                print(
                    f"  Holm[{family}] {name}: p={_fmt(row['p_value'], 4)} vs "
                    f"{_fmt(row['adjusted_threshold'], 4)}"
                    f" -> {'significant' if row['significant'] else 'NOT significant'}"
                )

    print("\nHYPOTHESES")
    for name, verdict in sorted(report["hypotheses"]["verdicts"].items()):
        if not verdict.get("testable", False):
            print(f"  {name}: NOT TESTABLE - {verdict.get('reason', '')}")
            continue
        interval = verdict.get("interval") or {}
        if "point" in interval:
            print(
                f"  {name}: {verdict.get('comparison', '')} = {_fmt(interval['point'])} "
                f"[{_fmt(interval['ci_low'])}, {_fmt(interval['ci_high'])}]  "
                f"p={_fmt(verdict.get('p_value'), 4)}  "
                f"{'SUPPORTED' if verdict.get('supported_before_correction') else 'not supported'}"
                " (before correction)"
            )
        else:
            body = {k: v for k, v in verdict.items() if k != "rationale"}
            print(f"  {name}: {json.dumps(body, default=str)[:150]}")
    correction = report["hypotheses"].get("family_wise_correction") or {}
    for name, row in sorted(correction.items()):
        print(
            f"  Holm {name}: p={_fmt(row['p_value'], 4)} vs {_fmt(row['adjusted_threshold'], 4)}"
            f" -> {'significant' if row['significant'] else 'NOT significant'}"
        )

    print("\nNOTES")
    for name, arm in sorted(report["arms"].items()):
        for note in arm.get("notes", ()):
            print(f"  [{name}] {note}")


def _pooled(run_ids: list[str]) -> tuple[list[dict], str]:
    """Records from one or more runs, refusing a pool that would confound.

    An ablation arm means "arm A minus one field", so every arm has to be read
    against arm A - and this project has already run arms of one ablation across
    two campaigns with DIFFERENT Channel A models (RX-047). Pooling those would
    silently mix a component contrast with a model swap, which is precisely the
    error the arms exist to avoid.

    So pooling is allowed only where the split and both channel bindings match
    exactly. It is a refusal rather than a warning because the resulting table
    looks entirely reasonable either way.
    """
    if len(run_ids) == 1:
        return load_records(run_ids[0]), run_ids[0]

    def binding(run: str) -> tuple:
        config = json.loads((RUNS_ROOT / run / "config.json").read_text(encoding="utf-8"))
        return (config.get("split"), config.get("natural_model"), config.get("program_model"))

    bindings = {run: binding(run) for run in run_ids}
    distinct = set(bindings.values())
    if len(distinct) > 1:
        lines = [
            f"  {run}: split={b[0]} natural={b[1]} program={b[2]}"
            for run, b in bindings.items()
        ]
        raise SystemExit(
            "\n".join(
                [
                    "Refusing to pool runs that do not share a split and channel bindings.",
                    *lines,
                    "",
                    "  Pooling these would mix a component ablation with a model swap,",
                    "  and the resulting table would look perfectly reasonable (RX-047).",
                ]
            )
        )

    records: list[dict] = []
    for run in run_ids:
        records.extend(load_records(run))
    return records, "+".join(sorted(run_ids))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run", required=True, action="append",
        help="run id under experiments/runs/. Repeatable: passing it more than once "
             "pools the runs into one report, which is how a single-field ablation "
             "spread over several campaigns is analysed. Pooling is REFUSED unless "
             "the runs share a split and identical channel bindings - see _pooled().",
    )
    parser.add_argument("--dataset", default=str(DATASET_PATH))
    parser.add_argument("--threshold", type=float,
                        help="an operating threshold already frozen on validation")
    parser.add_argument("--choose-threshold-on",
                        help="a DIFFERENT validation-split run id to fix the threshold on")
    parser.add_argument("--min-recall", type=float,
                        help="hold recall at or above this when choosing the threshold")
    parser.add_argument("--resamples", type=int, default=10_000)
    parser.add_argument("--out", help="report path (defaults under evaluation/reports/)")
    parser.add_argument(
        "--case-study",
        nargs="?",
        const="CASE_STUDY.md",
        help="also write the Module 27 case study (default CASE_STUDY.md)",
    )
    parser.add_argument("--case-study-arm", default="A")
    args = parser.parse_args()

    records, run_id = _pooled(args.run)
    if not records:
        print(f"no records for run(s) {args.run!r}", file=sys.stderr)
        return 1

    dataset = load_dataset(args.dataset)
    questions = {q.qid: q for q in dataset.questions if q.answer is not None}
    if not questions:
        print("the dataset has no gold answers; nothing can be graded", file=sys.stderr)
        return 1

    threshold = args.threshold
    threshold_source = "passed on the command line (frozen elsewhere)"
    if threshold is None and args.choose_threshold_on:
        if args.choose_threshold_on in args.run:
            print(
                "Refusing: the threshold may not be chosen on the run being "
                "reported. Fitting it to the data it is then scored on inflates "
                "precision and recall by construction (EVALUATION.md §5.2).",
                file=sys.stderr,
            )
            return 2
        validation_records = load_records(args.choose_threshold_on)
        if not validation_records:
            print(f"no records for run {args.choose_threshold_on!r}", file=sys.stderr)
            return 1
        threshold = choose_operating_threshold(
            validation_records, questions, min_recall=args.min_recall
        )
        threshold_source = f"selected on run {args.choose_threshold_on}"
        print(f"operating threshold {threshold:.4f} {threshold_source}")
    elif threshold is None:
        threshold_source = (
            "NOT SET - precision/recall/FPR/FNR are omitted rather than computed "
            "at an unrecorded threshold"
        )
        print(threshold_source)

    report = build_report(
        records, questions, threshold=threshold, resamples=args.resamples
    )
    report["run_id"] = run_id
    report["threshold_source"] = threshold_source
    report["generated_at"] = datetime.now(UTC).isoformat(timespec="seconds")

    REPORTS.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else REPORTS / f"results_{run_id}.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    summarise(report)
    print(f"\nfull report: {out}")

    if args.case_study:
        arm = args.case_study_arm
        cases = analyse(
            [r for r in records if r.get("arm") == arm and not r.get("error")],
            questions,
            arm=arm,
        ).cases
        study = build_case_study(
            cases,
            questions,
            records,
            arm=arm,
            sectors=SECTORS,
            detection=(report["arms"].get(arm) or {}).get("detection"),
        )
        Path(args.case_study).write_text(
            render_markdown(study, title="Case study: Indian annual reports"),
            encoding="utf-8",
        )
        print(f"case study:  {args.case_study}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
