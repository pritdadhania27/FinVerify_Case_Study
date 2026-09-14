"""Statistical protocol (EVALUATION.md §9) - what turns a point estimate into a finding.

A difference between two arms is not a result until it is shown to exceed what
resampling the same questions would produce by chance. On a 150-question dataset
(D23) that margin is wide: an AUROC gap of 0.05 between two arms is comfortably
inside the noise, and reporting it as an improvement would be the single most
likely way this project produces a false claim.

Everything here is deterministic given a seed. `random.Random(seed)` is used
rather than the module-level RNG so that two metrics computed in the same process
do not perturb each other's resamples, and so that a re-run of the analysis
reproduces the published interval exactly.

**Paired, not independent.** Arms are compared on the identical question set, so
resampling must draw the same question indices for both arms. Drawing
independently inflates the variance of the difference by treating shared
question difficulty as noise, and would hide real effects rather than invent
them - conservative, but wrong, and it would leave a genuine ablation result
unreportable.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass

__all__ = [
    "Interval",
    "bootstrap_ci",
    "paired_bootstrap_difference",
    "holm_bonferroni",
    "DEFAULT_RESAMPLES",
    "DEFAULT_SEED",
]

DEFAULT_RESAMPLES = 10_000
DEFAULT_SEED = 20260826


@dataclass(frozen=True)
class Interval:
    """A point estimate with its resampled interval.

    `undefined_resamples` counts draws where the statistic could not be computed
    - typically an AUROC resample that happened to contain no incorrect answers.
    Silently dropping those narrows the interval by conditioning on the draws
    that worked, so the count is carried and reported.
    """

    point: float | None
    low: float | None
    high: float | None
    resamples: int
    undefined_resamples: int = 0
    seed: int = DEFAULT_SEED

    @property
    def excludes_zero(self) -> bool:
        """The bar HYPOTHESES.md sets for 'supported'."""
        if self.low is None or self.high is None:
            return False
        return self.low > 0.0 or self.high < 0.0

    def as_dict(self) -> dict:
        return {
            "point": self.point,
            "ci_low": self.low,
            "ci_high": self.high,
            "resamples": self.resamples,
            "undefined_resamples": self.undefined_resamples,
            "seed": self.seed,
            "excludes_zero": self.excludes_zero,
        }


def _percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile. `values` must be sorted."""
    if not values:
        raise ValueError("no values")
    if len(values) == 1:
        return values[0]
    position = q * (len(values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    weight = position - lower
    return values[lower] * (1 - weight) + values[upper] * weight


def bootstrap_ci(
    items: Sequence,
    statistic: Callable[[list], float | None],
    *,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
    alpha: float = 0.05,
) -> Interval:
    """Percentile bootstrap CI for `statistic` over `items`.

    Resampling is over *questions*, which is the unit of independence here.
    Resampling over individual channel answers would treat two answers to the
    same question as independent observations, and they are not - they share the
    question, the evidence, and the retrieval.
    """
    items = list(items)
    if not items:
        return Interval(None, None, None, resamples, resamples, seed)

    point = statistic(items)
    rng = random.Random(seed)
    n = len(items)
    draws: list[float] = []
    undefined = 0
    for _ in range(resamples):
        sample = [items[rng.randrange(n)] for _ in range(n)]
        value = statistic(sample)
        if value is None:
            undefined += 1
        else:
            draws.append(value)

    if not draws:
        return Interval(point, None, None, resamples, undefined, seed)
    draws.sort()
    return Interval(
        point=point,
        low=_percentile(draws, alpha / 2),
        high=_percentile(draws, 1 - alpha / 2),
        resamples=resamples,
        undefined_resamples=undefined,
        seed=seed,
    )


def paired_bootstrap_difference(
    items: Sequence,
    statistic_a: Callable[[list], float | None],
    statistic_b: Callable[[list], float | None],
    *,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
    alpha: float = 0.05,
) -> tuple[Interval, float]:
    """CI on (A - B) and a two-sided bootstrap p-value, resampling questions once per draw.

    The p-value is the proportion of resamples whose difference falls on the
    opposite side of zero from the observed difference, doubled - the standard
    two-sided percentile-bootstrap convention. It is reported with the effect
    size, never alone: on 150 questions a p-value is a coarse instrument and the
    interval is the more informative object.
    """
    items = list(items)
    if not items:
        return Interval(None, None, None, resamples, resamples, seed), 1.0

    a, b = statistic_a(items), statistic_b(items)
    observed = None if (a is None or b is None) else a - b

    rng = random.Random(seed)
    n = len(items)
    draws: list[float] = []
    undefined = 0
    for _ in range(resamples):
        indices = [rng.randrange(n) for _ in range(n)]
        sample = [items[i] for i in indices]
        va, vb = statistic_a(sample), statistic_b(sample)
        if va is None or vb is None:
            undefined += 1
        else:
            draws.append(va - vb)

    if not draws or observed is None:
        return Interval(observed, None, None, resamples, undefined, seed), 1.0

    draws.sort()
    crossings = sum(1 for d in draws if (d <= 0.0 if observed > 0 else d >= 0.0))
    p_value = min(1.0, 2.0 * crossings / len(draws))
    return (
        Interval(
            point=observed,
            low=_percentile(draws, alpha / 2),
            high=_percentile(draws, 1 - alpha / 2),
            resamples=resamples,
            undefined_resamples=undefined,
            seed=seed,
        ),
        p_value,
    )


def holm_bonferroni(p_values: dict[str, float], *, alpha: float = 0.05) -> dict[str, dict]:
    """Holm-Bonferroni step-down across the H1-H5 family (EVALUATION.md §9).

    Five hypotheses tested at alpha = 0.05 each gives roughly a one-in-four
    chance of at least one spurious "supported" verdict. Holm controls the
    family-wise error rate while being uniformly more powerful than plain
    Bonferroni, and it needs no independence assumption - which matters here
    because H1 and H4 are tested on overlapping arms and are not independent.

    Returns each hypothesis with its adjusted threshold and verdict, including
    the ones that fail: EVALUATION.md §10 requires negative results to be
    reported with the same prominence as positive ones.
    """
    if not p_values:
        return {}
    ordered = sorted(p_values.items(), key=lambda kv: kv[1])
    m = len(ordered)
    results: dict[str, dict] = {}
    rejected_so_far = True
    for rank, (name, p) in enumerate(ordered, start=1):
        threshold = alpha / (m - rank + 1)
        # Step-down: once one test fails to reject, every larger p-value is
        # retained regardless of its own threshold.
        rejected = rejected_so_far and p <= threshold
        rejected_so_far = rejected
        results[name] = {
            "p_value": p,
            "rank": rank,
            "adjusted_threshold": threshold,
            "significant": rejected,
        }
    return results
