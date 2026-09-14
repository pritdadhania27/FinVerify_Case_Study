"""Every colour pair the UI actually renders clears the WCAG AA floor.

This exists because of what `--faint` was used for. At #858d9a it sat at 3.35:1
on a panel and 3.14:1 on a table header - under the 4.5:1 minimum for text its
size - and it is the colour of every table header, every stat label, every form
label, `.undefined-value` ("not measured"), `.risk-UNSCORED` ("no detector ran")
and the CONFIDENCE INTERVALS in the charts.

That list is the reason this is a research-integrity test and not a polish one.
The project's stated rule is that a caveat travels with its number and that an
undefined metric must never read as a zero. Both rules are enforced in the
markup and then undone in the stylesheet if the glyphs carrying them are the
least legible thing on the page.

Checked by computing the ratios from the declared tokens rather than by
rendering, so it runs without a browser and cannot drift from the stylesheet.

The grounds matter and cut in opposite directions per theme: dark glyphs lose
contrast on the DARKEST surface, light glyphs on the LIGHTEST one. Each pair is
therefore checked against every surface the element is actually drawn on.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS = Path(__file__).resolve().parent.parent / "frontend" / "src" / "index.css"

# WCAG 2.1: 4.5:1 for normal text, 3:1 for large (>=24px, or >=18.66px bold).
# Everything checked here is 10.5-15px, so the normal-text floor applies to all
# of it. Stated as a constant rather than inlined, because the temptation when a
# pair fails is to quietly call the text "large".
AA_NORMAL_TEXT = 4.5

# (foreground token, background token, what the reader sees)
PAIRS = [
    ("fg", "bg", "body text on the page"),
    ("fg", "surface", "body text in a panel"),
    ("fg-strong", "surface", "a heading in a panel"),
    ("muted", "bg", "a note on the page"),
    ("muted", "surface", "a note in a panel"),
    ("muted", "surface-sunken", "a note on a sunken ground"),
    # The pairs that were failing. Each is a caveat, not a decoration.
    ("faint", "surface", "a stat label, a form label, an interval"),
    ("faint", "bg", "the same, directly on the page"),
    ("faint", "surface-sunken", "a table column header"),
    ("accent", "bg", "a link on the page"),
    ("accent", "surface", "a link in a panel"),
    ("on-accent", "accent", "the label on an accent button"),
    ("accent-strong", "accent-soft", "accent text on its own tint"),
    ("risk-low", "risk-low-bg", "a LOW risk pill"),
    ("risk-mid", "risk-mid-bg", "a MEDIUM risk pill"),
    ("risk-high", "risk-high-bg", "a HIGH risk pill"),
    ("risk-low", "surface", "LOW risk text in a table"),
    ("risk-mid", "surface", "MEDIUM risk text in a table"),
    ("risk-high", "surface", "HIGH risk text in a table"),
]


def _balanced_block(text: str, start: int) -> str:
    """The brace-balanced block beginning at the first `{` at or after `start`."""
    open_at = text.index("{", start)
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_at : i + 1]
    raise AssertionError("unbalanced braces in index.css")


def _tokens(block: str) -> dict[str, str]:
    return {
        m.group(1): m.group(2)
        for m in re.finditer(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", block)
    }


def _relative_luminance(colour: str) -> float:
    channels = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _themes() -> dict[str, dict[str, str]]:
    css = CSS.read_text(encoding="utf-8")
    light = _tokens(_balanced_block(css, css.index(":root {")))
    dark_media = _balanced_block(css, css.index("@media (prefers-color-scheme: dark)"))
    # Dark only redefines tokens, so it inherits anything it does not restate -
    # which is also how the cascade resolves it in the browser.
    return {"light": light, "dark": {**light, **_tokens(dark_media)}}


def test_the_stylesheet_declares_both_themes():
    """Guards the guard: a parser that silently finds nothing would pass every
    assertion below while checking no colours at all."""
    themes = _themes()
    assert set(themes) == {"light", "dark"}
    for name, tokens in themes.items():
        assert len(tokens) > 15, f"{name} parsed only {len(tokens)} tokens"
    # The two themes must actually differ, or one block is being parsed twice.
    assert themes["light"]["bg"] != themes["dark"]["bg"]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_every_rendered_pair_clears_aa(theme):
    tokens = _themes()[theme]
    failures = []
    for fg, bg, what in PAIRS:
        assert fg in tokens, f"--{fg} is not declared"
        assert bg in tokens, f"--{bg} is not declared"
        ratio = contrast(tokens[fg], tokens[bg])
        if ratio < AA_NORMAL_TEXT:
            failures.append(
                f"{what}: --{fg} {tokens[fg]} on --{bg} {tokens[bg]} "
                f"= {ratio:.2f}:1, below {AA_NORMAL_TEXT}:1"
            )
    assert not failures, f"{theme} theme contrast failures:\n  " + "\n  ".join(failures)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_faint_stays_dimmer_than_muted(theme):
    """The fix must not flatten the hierarchy it was protecting.

    `--faint` carries secondary text and `--muted` carries notes; raising faint
    far enough to pass AA could easily overshoot into reading as emphasis, which
    would put labels and captions on the same visual level as prose.
    """
    tokens = _themes()[theme]
    ground = tokens["surface"]
    assert contrast(tokens["faint"], ground) < contrast(tokens["muted"], ground)
