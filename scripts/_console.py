"""Make stdout able to print the documents this project reads.

Windows consoles default to cp1252, which has no code point for the rupee sign.
Every script here prints extracted financial text, and every filing in the
corpus is denominated in rupees, so the default is a crash waiting for the first
line of real evidence:

    UnicodeEncodeError: 'charmap' codec can't encode character '\\u20b9'

It surfaced in `run_slice.py` the moment retrieval was pointed at a collection
that actually contained the corpus - the traceback was the *first* sign the fix
had worked, which is a poor way to learn it.

`PYTHONIOENCODING` cannot fix this from `.env`: the streams are already bound by
the time dotenv runs. Reconfiguring them is the only thing that works from
inside the process.

`errors="replace"` rather than `strict`: a console that cannot render a glyph
should show a placeholder, not abort a campaign that has been running for
hours. The data is unaffected - files are written with an explicit
`encoding="utf-8"` and never go through these streams.
"""

from __future__ import annotations

import sys
from pathlib import Path

__all__ = ["require_project_interpreter", "use_utf8"]

_VENV_PYTHON = Path(__file__).resolve().parent.parent / ".venv" / "Scripts" / "python.exe"


def require_project_interpreter() -> None:
    """Fail with the actual reason when launched by the wrong Python.

    On Windows, `run_campaign.py --resume ...` in a terminal does NOT use the
    venv - the file association hands the script to whichever interpreter is
    registered for `.py`, which here is the system 3.12. That interpreter has
    `dotenv` but not `langgraph`, so the run dies several imports deep with

        ModuleNotFoundError: No module named 'langgraph'

    which reads as a broken dependency rather than a wrong interpreter, and the
    obvious next move - pip install langgraph - installs it into the wrong
    environment and produces the *next* missing module instead.

    Imported before the backend packages by every entry script, so this message
    arrives instead of that one. It only warns: a legitimate run from another
    environment that genuinely has the dependencies is not blocked, because
    guessing wrong about someone's setup is worse than a printed caution.
    """
    if not _VENV_PYTHON.exists():
        return  # no venv to prefer; nothing to say
    try:
        running = Path(sys.executable).resolve()
    except (OSError, ValueError):
        return
    if running == _VENV_PYTHON.resolve():
        return
    print(
        f"WARNING: running under {running}\n"
        f"         not the project venv at {_VENV_PYTHON}\n"
        f"         ENGINEERING_RULES.md pins the 3.12 venv because the host interpreter\n"
        f"         does not have this project's dependencies. If the next error\n"
        f"         is a ModuleNotFoundError, this is why - re-run as:\n"
        f"             {_VENV_PYTHON} {' '.join(sys.argv)}\n",
        file=sys.stderr,
    )


def use_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # a redirected or wrapped stream may lack it
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # Not worth failing a run over. The scripts still work; some glyphs
            # may not render.
            pass
