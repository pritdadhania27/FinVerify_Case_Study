"""Financial value parsing and normalisation (spec Module 5).

This is where silent wrong answers originate. A scale error - reading a figure
stated in crore as though it were in millions - produces an answer that is wrong
by 10x yet looks entirely plausible, and no exception is raised. Indian filings
routinely mix crore, lakh, million and billion within a single document, so this
module treats scale as a first-class property rather than an afterthought.

Two deliberate choices:

* **Decimal, never float.** A project measuring numerical correctness cannot
  introduce binary floating-point representation error in its own foundation.
* **Every transformation is traced.** Spec section 13 requires transformations be
  traceable; a correctness judgment must be explainable back to the exact steps
  that produced it, including the steps that guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum

__all__ = [
    "detect_units",
    "Scale",
    "UnitKind",
    "FinancialValue",
    "ParseWarning",
    "parse_financial_value",
]


class Scale(Enum):
    """Magnitude multipliers. Values are exact Decimals, not floats."""

    UNIT = ("unit", Decimal(1))
    THOUSAND = ("thousand", Decimal(10) ** 3)
    LAKH = ("lakh", Decimal(10) ** 5)
    MILLION = ("million", Decimal(10) ** 6)
    CRORE = ("crore", Decimal(10) ** 7)
    BILLION = ("billion", Decimal(10) ** 9)
    TRILLION = ("trillion", Decimal(10) ** 12)

    def __init__(self, label: str, multiplier: Decimal) -> None:
        self.label = label
        self.multiplier = multiplier


class UnitKind(Enum):
    CURRENCY = "currency"
    PERCENT = "percent"
    RATIO = "ratio"
    COUNT = "count"
    UNKNOWN = "unknown"


class ParseWarning(Enum):
    """Conditions where parsing succeeded but the result deserves suspicion.

    These exist because silently guessing is the failure mode this module is
    built to prevent. A warning is not an error - it is a flag that a downstream
    consistency check should weight this value lower.
    """

    IRREGULAR_GROUPING = "irregular_digit_grouping"
    AMBIGUOUS_DECIMAL_SEPARATOR = "ambiguous_decimal_separator"
    SCALE_INHERITED_FROM_CONTEXT = "scale_inherited_from_context"
    NO_SCALE_DETERMINED = "no_scale_determined"
    FOOTNOTE_MARKER_STRIPPED = "footnote_marker_stripped"
    # A SECOND scale word sits beside the one that was used, and the two do not
    # agree. HDFC Bank's FY24 highlights page heads a column "Deposits (K Cr)"
    # and prints 23,79,786 - the same digits its own narrative on p.217 gives as
    # "23,79,786 crore". So "K Cr" there means crore, and reading it as
    # thousand-crore would be 1000x wrong.
    #
    # The parser matches "cr" and drops the "K", which is right for this filer
    # and right by accident: it discards a token it does not understand rather
    # than reasoning about it. A different filer using "K Cr" to mean what it
    # says would be silently misread by three orders of magnitude.
    #
    # So the value is kept and the ambiguity is RECORDED. A flagged figure a
    # consistency check can weight down beats a confident one nobody questions -
    # which is this module's whole premise.
    AMBIGUOUS_COMPOUND_SCALE = "ambiguous_compound_scale"


# Matching MUST be word-boundary anchored, not substring. Plain substring search
# reads "in(cr)ease of 500" as 500 crore - a 10,000,000x error of exactly the
# kind this module exists to prevent - and finds a rupee sign in "fi(rs)t half".
# Both were live bugs caught by probing rather than by the first test pass.
#
# Ordered longest-first so "million" is matched before "mn", and "crores" before
# "crore" leaves no stray "s".
_SCALE_PATTERNS: list[tuple[re.Pattern[str], Scale]] = [
    (re.compile(r"\btrillions?\b"), Scale.TRILLION),
    (re.compile(r"\bbillions?\b"), Scale.BILLION),
    (re.compile(r"\bmillions?\b"), Scale.MILLION),
    (re.compile(r"\bthousands?\b"), Scale.THOUSAND),
    (re.compile(r"\bcrores?\b"), Scale.CRORE),
    (re.compile(r"\blakhs?\b"), Scale.LAKH),
    (re.compile(r"\blacs?\b"), Scale.LAKH),
    (re.compile(r"\bbn\b\.?"), Scale.BILLION),
    (re.compile(r"\bmn\b\.?"), Scale.MILLION),
    (re.compile(r"\bcr\b\.?"), Scale.CRORE),
    (re.compile(r"'000s?"), Scale.THOUSAND),
]

_CURRENCY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\binr\b"), "INR"),
    (re.compile(r"\brs\b\.?"), "INR"),
    (re.compile(r"₹"), "INR"),
    (re.compile(r"\busd\b"), "USD"),
    (re.compile(r"\bus\$"), "USD"),
    (re.compile(r"\$"), "USD"),
    (re.compile(r"\beur\b"), "EUR"),
    (re.compile(r"€"), "EUR"),
    (re.compile(r"\bgbp\b"), "GBP"),
    (re.compile(r"£"), "GBP"),
]

_PERCENT_PATTERN = re.compile(r"%|\bper\s?cents?\b|\bpercentage\b")

# Scale-shaped tokens deliberately NOT treated as scales. Bare "k" is the only
# member: promoting it would read HDFC Bank's "Deposits (K Cr)" column as
# thousand-crore, when the same filing's narrative gives those exact digits as
# "crore" (p.4 vs p.217). Left out of _SCALE_PATTERNS so the reading stays
# right, matched here so the ambiguity is still reported.
_UNRECOGNISED_SCALE_TOKEN = re.compile(r"(?<![a-z0-9])k(?![a-z0-9])")

# PDF text extraction emits these instead of ASCII equivalents often enough that
# not handling them is a reliable source of parse failures.
_UNICODE_MINUS = {"−", "–", "—"}  # minus sign, en dash, em dash
_UNICODE_SPACES = {" ", " ", " ", " "}  # nbsp, figure, thin, narrow

# Trailing footnote markers: "1,234*", "1,234(a)", "1,234 #", "1,234†"
_FOOTNOTE_RE = re.compile(r"(?:\s*[\*\#†‡]+|\s*\([a-z]{1,2}\))+$", re.IGNORECASE)

# Western: 1,234,567  |  Indian: 12,34,567 (first group 1-2 digits, then pairs)
_WESTERN_GROUPING = re.compile(r"^\d{1,3}(?:,\d{3})+$")
_INDIAN_GROUPING = re.compile(r"^\d{1,2}(?:,\d{2})+,\d{3}$")


@dataclass(frozen=True)
class FinancialValue:
    """A parsed financial quantity that remembers how it was produced.

    `amount` is the magnitude exactly as written; `scale` is applied separately
    so that "1,234 crore" round-trips to its source form rather than collapsing
    immediately into 12340000000 and losing the reader's frame of reference.
    """

    amount: Decimal
    scale: Scale
    unit_kind: UnitKind
    currency: str | None = None
    raw_text: str = ""
    trace: tuple[str, ...] = field(default_factory=tuple)
    warnings: frozenset[ParseWarning] = field(default_factory=frozenset)

    def canonical(self) -> Decimal:
        """Value in base units: absolute currency units, or a fraction for percent.

        Percentages become fractions (25% -> 0.25) so that a percentage and a
        ratio expressing the same quantity compare equal. Comparing 25 against
        0.25 as though both were "the answer" is exactly the unit confusion the
        consistency engine exists to catch.
        """
        if self.unit_kind is UnitKind.PERCENT:
            return self.amount / Decimal(100)
        return self.amount * self.scale.multiplier

    def rescaled_to(self, scale: Scale) -> Decimal:
        """The magnitude this value would have if written at `scale`."""
        if self.unit_kind is UnitKind.PERCENT:
            raise ValueError("percentages have no magnitude scale")
        return (self.amount * self.scale.multiplier) / scale.multiplier

    def __str__(self) -> str:
        if self.unit_kind is UnitKind.PERCENT:
            return f"{self.amount}%"
        parts = [self.currency] if self.currency else []
        parts.append(str(self.amount))
        if self.scale is not Scale.UNIT:
            parts.append(self.scale.label)
        return " ".join(parts)


def detect_units(text: str) -> tuple[Scale | None, str | None]:
    """Scale and currency named in `text`, using this module's own vocabulary.

    Exists so nothing else has to keep a SECOND copy of the unit words. The
    program channel had one - five long-form labels, no abbreviations, no
    trillion - while Channel A parsed the same string through
    `parse_financial_value` and got the full set. The same unit text therefore
    resolved to different scales in the two channels, and the resulting clean
    power-of-ten gap was classified as SCALE_MISMATCH: a parser gap manufacturing
    the project's headline error class, inflating the disagreement rate and
    poisoning the very cell of the error taxonomy the research claims to detect.

    `_SCALE_PATTERNS` is ordered longest-first, so "thousand crore" resolves the
    same way here as it does in `parse_financial_value` rather than by whichever
    list a second implementation happened to check first.
    """
    lowered = (text or "").lower()
    scale = next((s for pattern, s in _SCALE_PATTERNS if pattern.search(lowered)), None)
    currency = next(
        (c for pattern, c in _CURRENCY_PATTERNS if pattern.search(lowered)), None
    )
    return scale, currency


def parse_financial_value(
    text: str,
    *,
    context_scale: Scale | None = None,
    context_currency: str | None = None,
    context_unit_kind: UnitKind | None = None,
) -> FinancialValue | None:
    """Parse one financial figure. Returns None if `text` holds no number.

    The `context_*` arguments carry information that lives outside the cell being
    parsed. Financial tables almost always state scale once, in a header
    ("Rs. in crore"), and then omit it from every cell beneath. Parsing a cell in
    isolation therefore cannot recover its own magnitude - the context must be
    threaded in, and when it is used the result is flagged
    SCALE_INHERITED_FROM_CONTEXT so a downstream check knows the scale was
    inherited rather than stated.
    """
    if text is None:
        return None

    trace: list[str] = []
    warnings: set[ParseWarning] = set()
    raw = text
    work = text

    for space in _UNICODE_SPACES:
        if space in work:
            work = work.replace(space, " ")
            trace.append("normalised unicode whitespace")
    work = work.strip()
    if not work:
        return None

    for dash in _UNICODE_MINUS:
        if dash in work:
            work = work.replace(dash, "-")
            trace.append(f"normalised unicode dash {dash!r} to '-'")

    work_lower = work.lower()

    # Currency must be detected before symbol stripping, since '$' and '₹' are
    # also the characters we are about to remove.
    currency = context_currency
    for pattern, code in _CURRENCY_PATTERNS:
        match = pattern.search(work_lower)
        if match:
            currency = code
            work_lower = pattern.sub(" ", work_lower, count=1)
            trace.append(f"detected currency {code} from {match.group(0)!r}")
            break

    unit_kind = context_unit_kind or UnitKind.UNKNOWN
    if _PERCENT_PATTERN.search(work_lower):
        unit_kind = UnitKind.PERCENT
        work_lower = _PERCENT_PATTERN.sub(" ", work_lower)
        trace.append("detected percentage")

    scale: Scale | None = None
    for pattern, sc in _SCALE_PATTERNS:
        match = pattern.search(work_lower)
        if match:
            scale = sc
            work_lower = pattern.sub(" ", work_lower, count=1)
            trace.append(f"detected scale {sc.label} from {match.group(0)!r}")
            break

    if scale is not None:
        # Anything scale-shaped LEFT OVER after the winner was consumed. The loop
        # above takes the first match and stops, so "23,79,786 K Cr" silently
        # becomes crore and "1 thousand crore" silently becomes 1,000 rather
        # than 10,000,000,000. Neither is corrected here - the corpus proves
        # crore is right for the case that actually occurs (29 times, all HDFC
        # Bank), and changing arithmetic to satisfy a case no document exercises
        # is how D10 got its two wrong conclusions. Flagged, not guessed.
        leftover = next(
            (
                m.group(0)
                for p, other in _SCALE_PATTERNS
                if other is not scale and (m := p.search(work_lower))
            ),
            None,
        )
        if leftover is None:
            # Bare "k" is NOT in _SCALE_PATTERNS, deliberately: adding it would
            # turn HDFC's "K Cr" into thousand-crore and make the one case that
            # actually occurs in this corpus 1000x wrong. But an unconsumed "k"
            # beside a real scale is still an ambiguity a reader should see, so
            # it is caught here rather than by promoting it to a scale.
            near = _UNRECOGNISED_SCALE_TOKEN.search(work_lower)
            leftover = near.group(0) if near else None
        if leftover:
            warnings.add(ParseWarning.AMBIGUOUS_COMPOUND_SCALE)
            trace.append(
                f"a second scale word {leftover.strip()!r} sits beside "
                f"{scale.label!r}; used {scale.label} and flagged the ambiguity"
            )

    if scale is None:
        if context_scale is not None:
            scale = context_scale
            warnings.add(ParseWarning.SCALE_INHERITED_FROM_CONTEXT)
            trace.append(f"inherited scale {scale.label} from context")
        else:
            scale = Scale.UNIT
            # Percentages and ratios have no magnitude scale, so an absent scale
            # is expected rather than suspicious.
            if unit_kind not in (UnitKind.PERCENT, UnitKind.RATIO):
                warnings.add(ParseWarning.NO_SCALE_DETERMINED)
            trace.append("no scale stated or inherited; assuming unit")

    stripped_footnote = _FOOTNOTE_RE.sub("", work_lower.strip())
    if stripped_footnote != work_lower.strip():
        warnings.add(ParseWarning.FOOTNOTE_MARKER_STRIPPED)
        trace.append("stripped trailing footnote marker")
    work_lower = stripped_footnote

    negative = False
    if "(" in work_lower and ")" in work_lower:
        inner = work_lower[work_lower.index("(") + 1 : work_lower.rindex(")")]
        if any(ch.isdigit() for ch in inner):
            negative = True
            work_lower = work_lower.replace("(", " ").replace(")", " ")
            trace.append("accounting parentheses -> negative")

    number_match = re.search(r"-?\d[\d,]*(?:\.\d+)?", work_lower)
    if not number_match:
        return None
    numeral = number_match.group(0)

    if numeral.startswith("-"):
        negative = True
        numeral = numeral[1:]
        trace.append("leading minus -> negative")

    if "," in numeral:
        integer_part = numeral.split(".")[0]
        if _WESTERN_GROUPING.match(integer_part):
            trace.append("western digit grouping")
        elif _INDIAN_GROUPING.match(integer_part):
            trace.append("indian digit grouping (lakh/crore)")
        else:
            # Both conventions strip to the same digits, so this does not change
            # the result - but an unrecognised pattern means the extraction may
            # have merged two cells, and that is worth flagging rather than
            # silently accepting.
            warnings.add(ParseWarning.IRREGULAR_GROUPING)
            trace.append(f"irregular digit grouping in {integer_part!r} - flagged")
        numeral = numeral.replace(",", "")

    try:
        amount = Decimal(numeral)
    except InvalidOperation:
        return None

    if negative:
        amount = -amount

    if unit_kind is UnitKind.UNKNOWN and currency is not None:
        unit_kind = UnitKind.CURRENCY
        trace.append("unit kind inferred as currency from currency symbol")

    return FinancialValue(
        amount=amount,
        scale=scale,
        unit_kind=unit_kind,
        currency=currency,
        raw_text=raw,
        trace=tuple(trace),
        warnings=frozenset(warnings),
    )
