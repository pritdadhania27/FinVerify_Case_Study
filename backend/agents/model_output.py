"""Reading what a model actually returned (spec Modules 8, 9).

Shared by both reasoning channels, and deliberately in a **third** module rather
than in either of them: `tests/test_channels.py` asserts that neither channel
imports the other, because a shared import is how coupling gets reintroduced
quietly and D1's independence claim is about exactly that. A utility both depend
on is fine; a dependency between them is not.

**Reasoning traces arrive in the content field.** Not every model puts its chain
of thought in a separate API field. Qwen on Groq replies literally

    <think>
    The user wants trade payables. Looking at E1 I see {row: ...} and 3,956.
    </think>
    {"answer": 3956, ...}

Both of this project's output contracts break on that. The natural channel's JSON
extraction saw the braces inside the think block and matched from the first to
the last, parsing as nothing - so with Qwen bound to Channel A **every** answer
came back UNAVAILABLE, which reads as "the channel does not work" rather than
"the parser cannot read this model". The program channel handed the tag to the
AST validator, which reported a syntax error - misattributing a formatting quirk
to the security layer.

Neither failure is loud, and both would have been mistaken for something else.
Swapping a model binding is not configuration; it changes the output contract.
"""

from __future__ import annotations

import json
import re

__all__ = ["strip_reasoning_trace", "balanced_objects", "extract_json_object"]

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S | re.I)
_THINK_OPEN = re.compile(r"<think>", re.I)
_FENCED = re.compile(r"```(?:json)?\s*(.+?)```", re.S)


def strip_reasoning_trace(text: str) -> str:
    """Remove a model's externalised chain of thought from its reply.

    Closed `<think>...</think>` blocks go first. An **unclosed** `<think>` is also
    handled, because a model that exhausts its budget mid-thought emits the
    opening tag and never the closing one: everything from that tag onward is
    dropped, except a JSON object that follows it.
    """
    cleaned = _THINK_BLOCK.sub(" ", text)
    opened = _THINK_OPEN.search(cleaned)
    if opened:
        tail = cleaned[opened.end() :]
        # An unclosed <think> means the reply was cut off mid-thought, and a
        # thought in progress is full of candidate figures the model is still
        # weighing. Taking the first brace object found there hands back a number
        # the model may have gone on to REJECT - measured on a real Qwen reply
        # that reasoned "First guess: {"answer": 3956}. No - the question asks
        # for the consolidated figure, so I should use", the channel reported
        # 3956 as its answer with available=True.
        #
        # That converts a genuine channel failure into a confident wrong number,
        # which then enters the agreement comparison as if it were real. Only a
        # tail that ENDS with its object is an answer; anything with prose after
        # the last object is deliberation, and the channel abstains.
        brace = tail.find("{") if tail.rstrip().endswith(("}", "```")) else -1
        cleaned = tail[brace:] if brace != -1 else cleaned[: opened.start()]
    return cleaned


def balanced_objects(text: str) -> list[str]:
    """Every balanced top-level `{...}` span, in order of appearance.

    A regex cannot do this correctly. `r"\\{.*\\}"` with DOTALL is greedy and
    spans from the first brace to the last, so a reply like
    `"<think>compute {a}/{b}</think>{...}"` yields one unparseable blob. This
    walks the text with a depth counter and skips over string literals, so braces
    inside JSON strings cannot throw the count off.
    """
    spans: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start != -1:
                spans.append(text[start : index + 1])
    return spans


def extract_json_object(text: str) -> dict | None:
    """The JSON object a model meant to return, or None.

    Candidates are tried **last first**. When a reply contains more than one
    object the final one is the answer; an earlier one is scaffolding the model
    talked itself out of, and preferring it would record a figure the model had
    already rejected.
    """
    if not text or not text.strip():
        return None

    cleaned = strip_reasoning_trace(text)
    candidates = [
        cleaned,
        *(m.group(1) for m in _FENCED.finditer(cleaned)),
        *reversed(balanced_objects(cleaned)),
    ]
    for candidate in candidates:
        try:
            parsed = json.loads(candidate.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None
