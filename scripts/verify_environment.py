"""Environment readiness gate (spec section 3).

Verifies every stack component by actually importing/invoking it and reports the
real version. Prints a table and exits non-zero if any REQUIRED check fails, so
this doubles as the acceptance test for Milestone 0.

Run:  .venv/Scripts/python scripts/verify_environment.py
"""

from __future__ import annotations

import importlib
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass

# (import name, distribution name for version lookup, required?)
PACKAGES: list[tuple[str, str, bool]] = [
    ("fastapi", "fastapi", True),
    ("pydantic", "pydantic", True),
    ("pydantic_settings", "pydantic-settings", True),
    ("sqlalchemy", "sqlalchemy", True),
    ("alembic", "alembic", True),
    ("psycopg", "psycopg", True),
    ("pymupdf", "pymupdf", True),  # `fitz` is the deprecated alias
    ("pdfplumber", "pdfplumber", True),
    ("camelot", "camelot-py", False),  # needs Ghostscript for lattice mode
    ("pytesseract", "pytesseract", True),
    ("cv2", "opencv-python-headless", False),
    ("torch", "torch", True),
    ("sentence_transformers", "sentence-transformers", True),
    ("transformers", "transformers", True),
    ("qdrant_client", "qdrant-client", True),
    ("rank_bm25", "rank-bm25", True),
    ("langgraph", "langgraph", True),
    ("langchain_core", "langchain-core", True),
    # Kept installed but optional: the runtime uses free OpenAI-compatible
    # providers over httpx (decision D8a), so the Anthropic SDK is not required.
    ("anthropic", "anthropic", False),
    ("pytest", "pytest", True),
    ("httpx", "httpx", True),
    ("dotenv", "python-dotenv", True),
    ("numpy", "numpy", True),
    ("pandas", "pandas", True),
]

TESSERACT_DEFAULT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


@dataclass
class Result:
    name: str
    status: str  # VERIFIED | MISSING | BLOCKED
    detail: str
    required: bool

    @property
    def failed(self) -> bool:
        return self.required and self.status != "VERIFIED"


def check_packages() -> list[Result]:
    from importlib.metadata import PackageNotFoundError, version

    results = []
    for module, dist, required in PACKAGES:
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - we want the real reason recorded
            results.append(Result(dist, "MISSING", f"import failed: {exc}", required))
            continue
        try:
            ver = version(dist)
        except PackageNotFoundError:
            ver = "installed (version unknown)"
        results.append(Result(dist, "VERIFIED", ver, required))
    return results


def check_tesseract() -> Result:
    """Tesseract ships as a binary; winget may not have refreshed PATH yet."""
    import pytesseract

    if not shutil.which("tesseract") and os.path.exists(TESSERACT_DEFAULT):
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_DEFAULT
    try:
        ver = pytesseract.get_tesseract_version()
    except Exception as exc:  # noqa: BLE001
        return Result("tesseract (binary)", "MISSING", str(exc), True)
    on_path = "on PATH" if shutil.which("tesseract") else f"via {TESSERACT_DEFAULT}"
    return Result("tesseract (binary)", "VERIFIED", f"{ver} ({on_path})", True)


def check_ghostscript() -> Result:
    """Camelot's lattice mode needs the Ghostscript binary, not just the wheel."""
    for exe in ("gswin64c", "gswin32c", "gs"):
        path = shutil.which(exe)
        if path:
            try:
                out = subprocess.run(
                    [path, "--version"], capture_output=True, text=True, timeout=15
                )
                return Result("ghostscript", "VERIFIED", out.stdout.strip(), False)
            except Exception as exc:  # noqa: BLE001
                return Result("ghostscript", "BLOCKED", str(exc), False)
    return Result(
        "ghostscript",
        "MISSING",
        "not found - camelot lattice mode unavailable (stream mode still works)",
        False,
    )


def check_docker() -> list[Result]:
    docker = shutil.which("docker")
    if not docker:
        return [Result("docker", "MISSING", "docker not on PATH", True)]
    try:
        out = subprocess.run(
            [docker, "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        return [Result("docker daemon", "BLOCKED", str(exc), True)]
    if out.returncode != 0:
        return [
            Result(
                "docker daemon",
                "BLOCKED",
                f"daemon not reachable: {out.stderr.strip()[:120]}",
                True,
            )
        ]
    results = [Result("docker daemon", "VERIFIED", out.stdout.strip(), True)]
    compose = subprocess.run(
        [docker, "compose", "version", "--short"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    results.append(
        Result(
            "docker compose",
            "VERIFIED" if compose.returncode == 0 else "MISSING",
            compose.stdout.strip() or compose.stderr.strip()[:120],
            True,
        )
    )
    return results


def check_llm_providers() -> list[Result]:
    """Credential presence only.

    Whether a key actually *works* is a separate question answered by
    `scripts/verify_llm_providers.py`, which makes real calls. Presence is not
    function, and this script must not imply otherwise.
    """
    from pathlib import Path

    from dotenv import load_dotenv

    # The repository's .env, not one relative to the working directory.
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    # Free tiers, no credit card. At least one is required for any reasoning.
    candidates = [
        ("GROQ_API_KEY", "console.groq.com - 30 req/min, 14,400 req/day"),
        ("GEMINI_API_KEY", "aistudio.google.com - ~15 req/min, 1,500 req/day"),
        ("NVIDIA_API_KEY", "build.nvidia.com - Channel B (D21, cross-vendor)"),
        ("OPENROUTER_API_KEY", "openrouter.ai - only ~50 req/day at zero balance"),
    ]
    present = [name for name, _ in candidates if os.environ.get(name)]

    results = [
        Result(
            name,
            "VERIFIED" if os.environ.get(name) else "MISSING",
            f"present (len={len(os.environ.get(name, ''))})"
            if os.environ.get(name)
            else f"not set - {hint}",
            False,  # individually optional; the requirement is "at least one"
        )
        for name, hint in candidates
    ]

    results.append(
        Result(
            "LLM provider (>=1)",
            "VERIFIED" if present else "MISSING",
            f"configured: {', '.join(present)}"
            if present
            else "no provider key set - set GROQ_API_KEY and/or GEMINI_API_KEY in .env",
            True,
        )
    )
    return results


def main() -> int:
    results: list[Result] = []
    results.append(
        Result(
            "python",
            "VERIFIED" if sys.version_info[:2] == (3, 12) else "BLOCKED",
            f"{platform.python_version()} ({sys.executable})",
            True,
        )
    )
    results.extend(check_packages())
    results.append(check_tesseract())
    results.append(check_ghostscript())
    results.extend(check_docker())
    results.extend(check_llm_providers())

    width = max(len(r.name) for r in results) + 2
    print(f"{'COMPONENT':<{width}} {'STATUS':<10} DETAIL")
    print("-" * (width + 60))
    for r in results:
        flag = "" if not r.failed else "  <-- REQUIRED"
        print(f"{r.name:<{width}} {r.status:<10} {r.detail}{flag}")

    failures = [r for r in results if r.failed]
    optional_gaps = [r for r in results if not r.required and r.status != "VERIFIED"]
    print()
    print(f"verified: {sum(r.status == 'VERIFIED' for r in results)}/{len(results)}")
    if optional_gaps:
        print(f"optional gaps: {', '.join(r.name for r in optional_gaps)}")
    if failures:
        print(f"REQUIRED FAILURES: {', '.join(r.name for r in failures)}")
        return 1
    print("all required components VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
