"""Where the project's files are, independent of where a process was started.

Every path constant in this repository used to be relative to the working
directory. That is invisible while everything runs from the repository root and
silently wrong the moment something does not, and the failure never looks like a
path failure:

- `scripts/review_gold.py` printed "no worksheet" and exited 0 rows reviewed,
  which is indistinguishable from a validator who reviewed nothing. It cost a
  real validation session.
- `TEST_ACCESS_LOG` is the audit trail for touching the sealed test split, and
  the guard's whole value is that *bypassing it leaves evidence in the
  repository* (spec §16). Written relative to the working directory, the
  evidence lands somewhere else - or nowhere - and the seal is decorative.
- `RUNS_ROOT` decides where campaign artifacts go and where a resume looks for
  them. A resume that finds no prior run does not fail; it starts again, and on
  a free tier that spends a day of quota.

None of these raise. They are the same shape as D-1, D36 and RX-020: a valid
operation, a normal-looking answer, and an empty or misdirected result.

`PROJECT_ROOT` is derived from this file's own location, so it is correct
whatever the working directory, and it is the single place to change if the
layout ever moves.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["PROJECT_ROOT", "project_path"]

# backend/core/paths.py -> backend/core -> backend -> <root>
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def project_path(*parts: str) -> Path:
    """A path under the repository root.

    Absolute inputs are returned unchanged, so a caller that already resolved a
    path - a `--worksheet` argument, say - is not silently re-rooted.
    """
    candidate = Path(*parts)
    return candidate if candidate.is_absolute() else PROJECT_ROOT.joinpath(*parts)
