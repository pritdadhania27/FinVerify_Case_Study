"""Paired single-field ablation contrasts: arm A minus exactly one field.

Every ablation arm in `evaluation/arms.py` is *arm A with one field changed*, so
an arm only means something when it is read against the arm A that shares its
binding and its questions. Producing these numbers by hand in a scratch script -
which is how RX-049's table was first computed - makes the project's one
supported result unreproducible, so the computation lives here.

**Two families, corrected separately.** `all` scores every question that carries
a risk score. `committed` restricts to questions where *both* arms actually
returned a figure. They are different claims: a numerical hallucination is a
committed wrong figure, and RX-048 established that 62% of what the all-rows
detector "detects" is the system's own abstention. Correcting them as one family
would be wrong - each is a family of contrasts against a single baseline, and a
contrast is only interesting if it survives within its own.

**The pairing is not optional.** Comparing arms over different question sets lets
a difference in question difficulty masquerade as a difference in method, and
comparing across bindings lets a model swap do the same (RX-047). This module
pairs by qid; the caller is responsible for passing arms from one binding, which
`analyse_campaign.py` enforces by refusing to pool mismatched runs.
"""

from __future__ import annotations

from evaluation.metrics.detection import roc_auc
from evaluation.metrics.statistics import (
    DEFAULT_RESAMPLES,
    holm_bonferroni,
    paired_bootstrap_difference,
)

__all__ = [
    "ABLATION_ARMS",
    "MIN_ERRORS_FOR_A_CONTRAST",
    "MIN_PAIRED_FOR_A_CONTRAST",
    "ablation_contrasts",
]

# What each arm removes, in the words the write-up uses. Keyed by arm so that a
# table generated from this module and a table written by hand cannot drift.
ABLATION_ARMS = {
    "B": "the natural-language channel",
    "C": "the executed-program channel",
    "D": "the consistency engine and arbiter",
    "E": "the verification agent",
    "F": "hybrid retrieval, leaving semantic only",
    "G": "the deterministic verifier",
    "H": "cross-model channels, leaving one model on both",
}

# Below this many paired questions a contrast is reported as untestable rather
# than to three decimal places. Matches report.MIN_STRATUM_FOR_A_CLAIM: the
# floor is about whether an interval means anything, not about which table it
# happens to appear in.
MIN_PAIRED_FOR_A_CONTRAST = 10

# An AUROC is a statement about ranking errors above correct answers, so its
# real sample size is the number of ERRORS, not the number of questions. The
# committed-only family on this corpus pairs ~20 questions carrying 3 errors -
# enough rows to clear the floor above and nowhere near enough to separate two
# arms. Contrasts below this are reported with `underpowered` set rather than
# withheld: the number is real, and the label is what stops it being read as a
# finding. Matches report.MIN_STRATUM_FOR_A_CLAIM, which counts the same thing.
MIN_ERRORS_FOR_A_CONTRAST = 10


def _rows(cases_by_arm: dict[str, list], arm: str) -> dict[str, tuple[float, int, bool]]:
    """qid -> (risk score, error label, committed) for one arm.

    A case with no risk score is dropped rather than defaulted: arms D and B1-B4
    are not detectors, and entering them at 0.5 would present a placeholder as a
    measurement. `committed` is `predicted is not None`, which is exactly the
    abstention flag the records carry.
    """
    return {
        case.qid: (case.risk_score, 0 if case.correct else 1, case.predicted is not None)
        for case in cases_by_arm.get(arm, ())
        if case.risk_score is not None
    }


def _auroc_difference(paired: list[tuple], *, resamples: int) -> tuple[dict, float | None]:
    """AUROC(baseline) - AUROC(arm) over paired rows, with its bootstrap interval."""
    interval, p = paired_bootstrap_difference(
        paired,
        lambda rows: roc_auc([r[0] for r in rows], [r[1] for r in rows]),
        lambda rows: roc_auc([r[2] for r in rows], [r[3] for r in rows]),
        resamples=resamples,
    )
    block = interval.as_dict()
    block["n"] = len(paired)
    block["p_value"] = p
    block["supported_before_correction"] = bool(
        interval.excludes_zero and (interval.point or 0) > 0
    )
    return block, p


def ablation_contrasts(
    cases_by_arm: dict[str, list],
    *,
    baseline: str = "A",
    resamples: int = DEFAULT_RESAMPLES,
) -> dict:
    """Every present ablation arm contrasted against the baseline, both families.

    Returns a block per arm carrying `all` and `committed`, plus a Holm
    correction per family. An arm that is absent, has no risk score, or shares
    too few questions with the baseline is reported as untestable with the
    reason - an omitted row reads as an oversight, and "not testable here" is
    itself a result (EVALUATION.md §10).
    """
    base = _rows(cases_by_arm, baseline)
    contrasts: dict[str, dict] = {}
    p_values: dict[str, dict[str, float]] = {"all": {}, "committed": {}}

    for arm in sorted(ABLATION_ARMS):
        if arm == baseline or arm not in cases_by_arm:
            continue
        name = f"{baseline}-{arm}"
        other = _rows(cases_by_arm, arm)
        block: dict = {"baseline": baseline, "arm": arm, "removes": ABLATION_ARMS[arm]}

        if not other:
            block["all"] = {
                "testable": False,
                "reason": f"arm {arm} carries no risk score; it is not a detector",
            }
            block["committed"] = dict(block["all"])
            contrasts[name] = block
            continue

        shared = [qid for qid in base if qid in other]
        for family in ("all", "committed"):
            rows = [
                (base[qid][0], base[qid][1], other[qid][0], other[qid][1])
                for qid in shared
                if family == "all" or (base[qid][2] and other[qid][2])
            ]
            if len(rows) < MIN_PAIRED_FOR_A_CONTRAST:
                block[family] = {
                    "testable": False,
                    "n": len(rows),
                    "reason": (
                        f"only {len(rows)} question(s) are shared by {baseline} "
                        f"and {arm} in the {family} family; not enough to test this"
                    ),
                }
                continue
            measured, p = _auroc_difference(rows, resamples=resamples)
            measured["testable"] = True
            measured["comparison"] = f"AUROC({baseline}) - AUROC({arm})"
            errors = sum(r[1] for r in rows)
            measured["errors"] = errors
            if errors < MIN_ERRORS_FOR_A_CONTRAST:
                measured["underpowered"] = True
                measured["reason"] = (
                    f"{errors} error(s) across {len(rows)} paired question(s): an "
                    "AUROC is a ranking over errors, so this contrast cannot "
                    "separate two arms however small its p-value is"
                )
            block[family] = measured
            if p is not None:
                p_values[family][name] = p

        contrasts[name] = block

    return {
        "baseline": baseline,
        "contrasts": contrasts,
        "family_wise_correction": {
            family: holm_bonferroni(values) for family, values in p_values.items() if values
        },
        "note": (
            "Holm-Bonferroni is applied WITHIN each family, not across both. "
            "`all` and `committed` are different claims about the same arms - "
            "the second is the subset where a numerical hallucination can occur "
            "at all (RX-048) - so a contrast must survive inside its own family. "
            "Arms are paired by question id; pooling arms across channel "
            "bindings would let a model swap read as a component effect "
            "(RX-047)."
        ),
    }
