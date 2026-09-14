"""Evidence-grounded explanation (spec Module 16).

**The explanation is derived, never generated.** Asking a model to write "why is
this answer risky?" would put a language model in the one place the system claims
to be trustworthy, and it would be free to produce a fluent account that does not
match the computation. That failure is worse than no explanation: a wrong number
with a convincing justification is more dangerous than a wrong number on its own,
and this project exists because plausible-looking financial figures are hard to
catch. So every sentence below is a template over recorded state, and every claim
in it is checkable against the run artifact.

The consequence is that explanations read as terse rather than fluent. That is
the trade being made, and it is the right way round: the reader can verify each
line against the artifact, which is the property that matters for a system whose
output is a number someone will act on.

Four things an explanation has to answer, in the order a sceptical reader asks
them:

1. What is the answer, and where in the document does it come from?
2. What did each independent channel say?
3. Why is the risk what it is - which specific signal fired?
4. What would change it? An explanation that cannot say what would make the
   system more confident is a description, not an explanation.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.verification.confidence import FacetStatus, RiskAssessment
from backend.verification.consistency import ConsistencyReport, Verdict

__all__ = ["Explanation", "explain", "render_text"]


# Keyed by `RiskAssessment.facets`, which is a {name: FacetStatus} mapping over
# spec §23's four dimensions.
#
# Each sentence must describe the state `confidence._facets` actually assigns.
# agreement PARTIAL is an UNCERTAIN verdict (fewer than two figures) and evidence
# PARTIAL is a usable-channel shortfall. They used to read "the channels broadly
# agree but not within tolerance" and "evidence was retrieved but thin" - so a live
# answer whose program channel had timed out told the user the channels broadly
# agreed, on the verdict that is most common in every campaign.
_FACET_PROSE = {
    "agreement": {
        FacetStatus.VERIFIED: "the independent channels produced the same figure",
        FacetStatus.PARTIAL: (
            "fewer than two channels produced a figure, so the answer could not be "
            "cross-checked"
        ),
        FacetStatus.FAILED: "the channels produced different figures",
        FacetStatus.NOT_APPLICABLE: "fewer than two channels produced a figure to compare",
    },
    "unit": {
        FacetStatus.VERIFIED: "the channels agree on units and scale",
        FacetStatus.PARTIAL: "units were not fully stated by every channel",
        FacetStatus.FAILED: (
            "the channels disagree on units or scale - the failure mode that "
            "produces a plausible 10x-100x error"
        ),
        FacetStatus.NOT_APPLICABLE: "no unit comparison was possible",
    },
    "evidence": {
        FacetStatus.VERIFIED: "the answer is grounded in retrieved evidence",
        FacetStatus.PARTIAL: (
            "evidence was retrieved, but a channel that could have used it produced "
            "no figure"
        ),
        FacetStatus.FAILED: (
            "no evidence was retrieved, so the answer cannot be grounded whatever "
            "the channels said about it"
        ),
        FacetStatus.NOT_APPLICABLE: "evidence grounding was not assessed",
    },
    "arithmetic": {
        FacetStatus.VERIFIED: "an independent deterministic calculation agrees",
        FacetStatus.PARTIAL: "the deterministic verifier ran but could not conclude",
        FacetStatus.FAILED: (
            "the deterministic verifier CONTRADICTS the reasoning channels - the "
            "both-agree-and-both-wrong case"
        ),
        FacetStatus.NOT_APPLICABLE: (
            "there is no arithmetic to check: this is a lookup, so the "
            "deterministic verifier does not apply and its silence is not a gap"
        ),
    },
}


@dataclass(frozen=True)
class Explanation:
    """A structured account of one answer. Every field traces to recorded state."""

    answer: str | None
    answer_source: str | None
    risk_score: float | None
    band: str
    citations: tuple[str, ...] = ()
    channel_statements: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    what_would_change_it: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "answer": self.answer,
            "answer_source": self.answer_source,
            "risk_score": self.risk_score,
            "band": self.band,
            "citations": list(self.citations),
            "channel_statements": list(self.channel_statements),
            "reasons": list(self.reasons),
            "what_would_change_it": list(self.what_would_change_it),
            "caveats": list(self.caveats),
        }


def _channel_line(name: str, result) -> str:
    if result is None:
        return f"{name}: not run in this configuration"
    answer = result.answer
    if not answer.applicable:
        return f"{name}: does not apply to this question ({answer.failure_reason or 'n/a'})"
    if not answer.available or answer.value is None:
        return f"{name}: produced no figure ({answer.failure_reason or 'unknown reason'})"
    return f"{name}: {answer.value}"


def _remedies(
    report: ConsistencyReport | None,
    risk: RiskAssessment | None,
    evidence_blocks: int,
) -> list[str]:
    """What would actually move the risk score.

    Concrete and specific, because "gather more evidence" is not actionable. A
    reader who cannot act on an explanation has been given a status message.
    """
    out: list[str] = []
    if evidence_blocks == 0:
        out.append(
            "Retrieve evidence for this question. Nothing else can help: with no "
            "evidence the answer is ungrounded regardless of channel agreement."
        )
        return out
    if report is None:
        out.append(
            "Enable the consistency engine. This configuration has no cross-check, "
            "so there is no signal to raise or lower."
        )
        return out
    if report.verdict is Verdict.UNCERTAIN:
        out.append(
            "Get a second channel to produce a figure. The verdict is UNCERTAIN "
            "because fewer than two channels answered, which is missing "
            "corroboration rather than evidence of an error."
        )
    if report.usable_shortfall:
        out.append(
            f"{report.usable_shortfall} channel(s) could have answered and did "
            "not; fixing that failure would reduce the coverage penalty without "
            "any change to the reasoning."
        )
    if report.verdict is Verdict.DISAGREE:
        out.append(
            "Resolve the disagreement by checking the cited pages directly. The "
            "channels reached different figures from the same evidence, so at "
            "least one reading of that evidence is wrong."
        )
    if risk is not None and risk.features.arbiter_abstained:
        out.append(
            "The arbiter declined to resolve the disagreement, so the risk stayed "
            "high. Its abstention is a refusal to guess, not a failure."
        )
    if not out:
        out.append(
            "Nothing in this run raised a flag. Residual risk is whatever both "
            "channels and the evidence could be wrong about together - which the "
            "system cannot see by construction."
        )
    return out


def explain(result) -> Explanation:
    """Explain one `PipelineResult`.

    Takes the result object rather than the run record so the explanation is
    produced from the same objects the verdict was, with no serialisation step
    in between where the two could drift apart.
    """
    risk: RiskAssessment | None = result.risk
    report: ConsistencyReport | None = result.report
    blocks = list(result.blocks or ())
    answer = result.answer

    citations = tuple(
        f"{block.ref}: {block.citation}" for block in blocks if block.citation
    )

    statements = [
        _channel_line("Channel A (natural language)", result.natural),
        _channel_line("Channel B (executed program)", result.program),
        _channel_line("Deterministic verifier", result.deterministic),
    ]
    if result.verification is not None:
        verification = result.verification
        statements.append(
            f"Arbiter: {verification.resolution.value}"
            + (f" -> {verification.value}" if verification.value is not None else "")
        )

    reasons: list[str] = []
    if risk is not None:
        for name, status in risk.facets.items():
            prose = _FACET_PROSE.get(name, {}).get(status)
            reasons.append(f"{name}: {status.value}" + (f" - {prose}" if prose else ""))
        reasons.extend(risk.notes)
    elif report is not None:
        reasons.append(f"consistency verdict {report.verdict.name} at score {report.score:.3f}")
    else:
        reasons.append(
            "this configuration produces no risk score, so no reliability claim "
            "is made about this answer"
        )

    caveats: list[str] = []
    if risk is not None and not risk.calibrated:
        caveats.append(
            "The risk score is UNCALIBRATED. It ranks answers by relative risk, "
            "which is what AUROC measures, but the number itself is not a "
            "probability and must not be read as one."
        )
    if answer is not None and result.answer_source == "deterministic":
        caveats.append(
            "The answer comes from the deterministic verifier because neither "
            "reasoning channel produced a figure."
        )
    if not blocks:
        caveats.append("No evidence was retrieved for this question.")

    return Explanation(
        answer=str(answer) if answer is not None else None,
        answer_source=result.answer_source,
        risk_score=result.risk_score,
        band=risk.band() if risk is not None else "UNSCORED",
        citations=citations,
        channel_statements=tuple(statements),
        reasons=tuple(reasons),
        what_would_change_it=tuple(_remedies(report, risk, len(blocks))),
        caveats=tuple(caveats),
    )


def render_text(explanation: Explanation) -> str:
    """Plain-text rendering, for a terminal or a case-study appendix."""
    lines: list[str] = []
    answer = explanation.answer or "no answer produced"
    score = (
        f"{explanation.risk_score:.3f}" if explanation.risk_score is not None else "not scored"
    )
    lines.append(f"ANSWER: {answer}")
    lines.append(f"  source: {explanation.answer_source or 'none'}")
    lines.append(f"  risk:   {score} ({explanation.band})")

    if explanation.citations:
        lines.append("\nEVIDENCE")
        lines.extend(f"  {c}" for c in explanation.citations)

    lines.append("\nWHAT EACH CHANNEL SAID")
    lines.extend(f"  {s}" for s in explanation.channel_statements)

    lines.append("\nWHY")
    lines.extend(f"  {r}" for r in explanation.reasons)

    lines.append("\nWHAT WOULD CHANGE IT")
    lines.extend(f"  {r}" for r in explanation.what_would_change_it)

    if explanation.caveats:
        lines.append("\nCAVEATS")
        lines.extend(f"  {c}" for c in explanation.caveats)
    return "\n".join(lines)
