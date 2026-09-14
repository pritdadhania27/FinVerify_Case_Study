"""Run the evaluation campaign (spec Modules 23, 24, 28).

    python scripts/run_campaign.py --arms A B5 --split validation
    python scripts/run_campaign.py --resume campaign_20260826T120000Z
    python scripts/run_campaign.py --budget-only            # cost it, spend nothing

**Budget first.** `--budget-only` prints the request count per arm against the
observed daily limits before anything is spent. On a free tier the binding
constraint is requests per day, not money (D14), and a campaign that dies 70%
through a quota day has wasted more than it spent.

**Resumable.** The run artifact is keyed on (arm, question) and appended to, so
re-running with `--resume <run_id>` skips completed work and re-spends nothing.
Quota exhaustion stops the run cleanly and prints the resume command.

**Validated gold only.** The dataset layer refuses to hand out PENDING questions,
so a campaign cannot be run against candidates by accident. If nothing is
validated yet, this script says so and exits rather than running against zero
questions and reporting a successful empty campaign.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The repository's .env, not one relative to wherever the process started:
# run from elsewhere and every API key silently goes missing.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

from scripts._console import require_project_interpreter, use_utf8  # noqa: E402

use_utf8()
require_project_interpreter()

from dotenv import load_dotenv

from backend.agents.orchestrator import PipelineDeps
from backend.rag.embedding import Embedder
from backend.rag.indexing import QdrantIndex
from backend.rag.manifest import IndexConfigurationError, preflight
from backend.retrieval.hybrid import HybridRetriever
from backend.services.llm.registry import (
    build_provider,
    channels_are_independent,
    resolve_channel,
)
from evaluation.arms import ALL_ARMS, validate_all
from evaluation.dataset import DATASET_ROOT, build_split, load_dataset
from experiments.campaign import RUNS_ROOT, estimate_requests, run_campaign

DATASET_PATH = DATASET_ROOT / "finverify_ind_v1.json"

# A quota stop is NOT a failure, and it is not success either: the run is
# incomplete, resumable, and should be retried when the allowance refills. It
# gets its own exit code so a caller can tell it apart from a real error without
# parsing stdout. `run_campaign_unattended.py` imports this constant rather than
# hard-coding 3, because a supervisor that mistakes this for a genuine failure
# stops a week-long campaign after its first quota stop - which is exactly what
# happened on 2026-09-01, four rows into a 180-row run.
STOPPED_EARLY = 3


def preflight_models(bindings: list[tuple[str, object]]) -> str | None:
    """One real completion per distinct binding. Returns the failures, or None.

    `/models` listing a name is not evidence it is servable - on 2026-09-03 it
    listed `openai/gpt-oss-120b` hours after that model began returning 410 Gone,
    and lists `qwen/qwen3.6-27b`, which 404s. Only a completion establishes it,
    so this makes one. Deduplicated, because the arbiter usually shares the
    natural channel's binding and there is no sense paying twice.
    """
    from backend.services.llm import build_provider
    from backend.services.llm.base import Message, Role

    checked: dict[tuple[str, str], str | None] = {}
    for role, binding in bindings:
        key = (binding.provider, binding.model)
        if key in checked:
            continue
        try:
            build_provider(binding.provider).complete(
                messages=[Message(role=Role.USER, content="Reply with the digit 7 only.")],
                model=binding.model,
                temperature=0,
                # NOT a token or two. These are reasoning models: they spend
                # completion budget on hidden reasoning before emitting
                # anything, so a tight budget returns empty with
                # finish_reason=length and the preflight refuses a HEALTHY
                # binding. 256 was enough for every model in the registry when
                # this was written; the health check observes 115 for
                # gpt-oss-20b.
                max_tokens=256,
            )
            checked[key] = None
        except Exception as exc:  # noqa: BLE001 - any failure here is disqualifying
            checked[key] = f"  {role} -> {binding.label}\n      {type(exc).__name__}: {exc}"
    failures = [v for v in checked.values() if v]
    return "\n".join(failures) if failures else None


def live_freeze_tags() -> list[str] | None:
    """Methodology-freeze tags that are not marked VOID. None if git cannot say.

    D46 wrote its four-step precondition for touching the test split as prose in
    a decision entry, and prose blocks nothing: on 2026-09-05 the split was
    evaluated against `methodology-freeze-v1`, which D46 itself had voided two
    days earlier, and the only thing that could have stopped it was someone
    re-reading D46 before typing the command (D48). This is that check made
    mechanical. It is the same shape as `preflight_models` - a claim in a config
    file is not evidence, so go and look.

    A freeze is 'live' when its annotation does not say VOID. Fails closed: no
    git, no answer, no test run.
    """
    import subprocess

    try:
        out = subprocess.run(
            # contents:subject, not contents - a tag annotation is many lines and
            # only the subject is guaranteed to be one, which keeps this a clean
            # line-per-tag parse.
            ["git", "for-each-ref", "--format=%(refname:short)\t%(contents:subject)",
             "refs/tags/methodology-freeze-*"],
            capture_output=True, text=True, timeout=15,
            cwd=str(Path(__file__).resolve().parent.parent), check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    live = []
    for line in out.splitlines():
        name, tab, subject = line.partition("\t")
        # No tab means this is not a tag record - it is a continuation line from
        # a multi-line annotation. Without this, such a line is read as a tag
        # name with an empty subject, which contains no "VOID" and so counts as
        # a LIVE freeze. The gate would then pass on the strength of a sentence
        # inside the void tag's own body.
        if tab and name.strip() and "VOID" not in subject.upper():
            live.append(name.strip())
    return live


def main() -> int:
    # BEFORE the parser. argparse evaluates `default=os.environ.get(...)` at
    # add_argument() time, so calling this afterwards reads .env too late and
    # the retrieval defaults below silently win. Until this line moved, .env
    # naming the wrong collection was ignored here purely by accident - the one
    # reason a campaign would not have been run against a one-company index.
    load_dotenv(_ENV_FILE)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arms", nargs="+", default=["A"], help="arm names, e.g. A B5 H")
    parser.add_argument("--split", default="validation",
                        choices=["train", "validation", "test"])
    parser.add_argument("--dataset", default=str(DATASET_PATH))
    parser.add_argument("--limit", type=int, help="first N questions (for a smoke run)")
    parser.add_argument("--resume", help="run id to continue")
    parser.add_argument("--budget-only", action="store_true", help="cost it, spend nothing")
    parser.add_argument("--include-ambiguous", action="store_true",
                        help="include the D22 ambiguity subset (scored separately)")
    parser.add_argument("--reason", default="", help="required for the test split")
    parser.add_argument("--collection", default=os.environ.get("QDRANT_COLLECTION",
                                                              "finverify_e5"))
    parser.add_argument("--model", default=os.environ.get("EMBEDDING_MODEL",
                                                         "intfloat/e5-base-v2"))
    args = parser.parse_args()

    problems = validate_all()
    if problems:
        for name, issues in problems.items():
            print(f"arm {name}: {'; '.join(issues)}", file=sys.stderr)
        return 2

    unknown = [a for a in args.arms if a not in ALL_ARMS]
    if unknown:
        print(f"unknown arm(s) {unknown}; known: {sorted(ALL_ARMS)}", file=sys.stderr)
        return 2
    arms = [ALL_ARMS[a] for a in args.arms]

    # Before build_split, which is what appends to the access log: a refused
    # attempt should leave no trace of having touched the test set, because the
    # log's whole value is that every line in it is a real access.
    if args.split == "test":
        live = live_freeze_tags()
        if live is None:
            print(
                "\nRefusing to run the test split: git could not be asked which\n"
                "  methodology freezes are live, and this gate fails closed.",
                file=sys.stderr,
            )
            return 2
        if not live:
            print(
                "\nRefusing to run the test split: there is no live methodology freeze.\n"
                "  Every methodology-freeze-* tag is marked VOID in its annotation.\n\n"
                "  The test split is evaluated once per FROZEN methodology, and a voided\n"
                "  freeze is not one. On 2026-09-05 this ran against methodology-freeze-v1\n"
                "  two days after D46 voided it, because nothing checked (D48).\n\n"
                "  Freeze first, then tag, then evaluate:\n"
                '      git tag -a methodology-freeze-vN -m "<what is frozen, and why now>"',
                file=sys.stderr,
            )
            return 2
        print(f"  methodology freeze: {', '.join(live)}\n")

    path = Path(args.dataset)
    if not path.exists():
        print(f"no dataset at {path}; run scripts/build_finverify_ind.py generate",
              file=sys.stderr)
        return 1
    dataset = load_dataset(path)
    questions = build_split(
        dataset, args.split,
        include_ambiguous=args.include_ambiguous,
        reason=args.reason,
    )
    if args.limit:
        questions = questions[: args.limit]

    if not questions:
        print(
            f"the {args.split} split has no VALIDATED questions.\n"
            f"  {len(dataset)} question(s) exist, {len(dataset.pending())} are pending "
            "human validation (spec 16).\n"
            "  Run: scripts/build_finverify_ind.py export  ->  validate  ->  import",
            file=sys.stderr,
        )
        return 1

    payload = [
        {
            "id": q.qid,
            "question": q.question,
            # Carried per question. Retrieval scoped to the issuer is worth
            # +0.409 Recall@10 on this corpus (RX-012), and the dataset is the
            # authority on which company a question is about.
            "company": q.company,
            "document_id": q.document_id,
            "stratum": q.stratum,
            "ambiguous": q.ambiguous,
            "question_type": q.question_type,
            # Only the oracle arm reads this, to resolve gold spans to the
            # chunks containing them. In memory only - it is not written to the
            # run artifact, and no other arm is given it, so a retrieving arm
            # cannot see the gold through this field.
            "evidence": q.evidence,
        }
        for q in questions
    ]

    budget = estimate_requests(arms, len(payload))
    print(f"{len(payload)} question(s) x {len(arms)} arm(s)")
    print(f"budget: {budget.total} requests (upper bound; the arbiter fires only "
          f"on disagreement)")
    for name, count in sorted(budget.per_arm.items()):
        print(f"  {name}: {count}")

    # Tokens, not requests, end a free-tier day (RX-022), and they are counted
    # PER PROVIDER because the limits are (RX-023). Channel B runs on NVIDIA and
    # Channel A on Groq (D21), so pooling them charges Groq for calls it never
    # serves - the opposite error to the one RX-022 fixed, and just as wrong.
    from backend.services.llm.registry import PROVIDER_SPECS

    from experiments.campaign import tokens_per_call

    per_call = tokens_per_call()
    calls = budget.calls_by_provider()
    tokens = budget.tokens_by_provider()
    print(f"\nestimated cost: {per_call:,} tokens per call "
          f"({per_call - 1780:,} of it reserved completion budget)")
    worst_days = 0.0
    for provider_name in sorted(calls):
        spec = PROVIDER_SPECS.get(provider_name)
        limit = spec.rate_limit if spec else None
        tpd = limit.tokens_per_day if limit else None
        line = (
            f"  {provider_name:<9}{calls[provider_name]:>6} calls  "
            f"{tokens[provider_name]:>12,} tokens  "
        )
        if tpd:
            days = tokens[provider_name] / tpd
            worst_days = max(worst_days, days)
            line += f"-> {days:>6.1f} days at {tpd:,}/day"
        else:
            line += "-> daily token cap UNOBSERVED for this provider"
        print(line)
    if worst_days:
        print(f"\n  the campaign takes at least {worst_days:.1f} days on the slowest "
              f"provider.")
        if worst_days > 1:
            print("  Levers, in order of effect: fewer questions, fewer arms, a lower")
            print("  LLM_MAX_TOKENS (70% of a call is reserved completion budget), or")
            print("  rebalancing roles across providers. See TODO.md - this is a scope")
            print("  decision, not an engineering one.")

    if args.budget_only:
        print("\n--budget-only: nothing was spent")
        return 0

    independent, note = channels_are_independent()
    print(f"\nchannel independence: {'OK' if independent else 'VIOLATED'} - {note}")
    same_model_arms = [a.name for a in arms if a.same_model_both_channels]
    if not independent and set(a.name for a in arms) - set(same_model_arms):
        print(
            "Refusing to run: the channels are bound to the same model, so their "
            "agreement measures sampling noise rather than independent "
            "corroboration (D1). Arm H is the deliberate same-model ablation and "
            "may be run on its own.",
            file=sys.stderr,
        )
        return 2

    natural = resolve_channel("natural_channel")
    program = resolve_channel("program_channel")
    verifier = resolve_channel("verification_agent")
    print(f"Channel A: {natural.label}\nChannel B: {program.label}\nArbiter:   {verifier.label}\n")

    # Before the embedding model and BM25 cache, which take minutes: a dead
    # binding should cost seconds, not a startup and then a campaign of
    # provider errors.
    print("  verifying every binding with a real call...")
    dead = preflight_models(
        [("natural_channel", natural), ("program_channel", program),
         ("verification_agent", verifier)]
    )
    if dead:
        print(
            f"\nRefusing to run: a channel binding is not servable.\n{dead}\n\n"
            "  A model id that `--list` still shows can be retired: on 2026-09-03\n"
            "  gpt-oss-120b was listed for hours after it began returning 410.\n"
            "  Check the current lineup and rebind in .env:\n"
            "      .venv\\Scripts\\python.exe scripts\\verify_llm_providers.py --list",
            file=sys.stderr,
        )
        return 2
    print("  all bindings answered.\n")

    embedder = Embedder(args.model, device=os.environ.get("EMBEDDING_DEVICE", "cpu"))
    index = QdrantIndex(collection=args.collection, dimension=embedder.dimension)

    # An empty-collection check was never enough. The collection can be full,
    # reachable and healthy while holding one company's chunks embedded by a
    # different model - which is what D-1 was, and it would have been read as a
    # failure of the method rather than of the configuration. Refuses to spend a
    # day of quota on an index that cannot answer the questions.
    try:
        manifest = preflight(
            index,
            embedding_model=args.model,
            required_companies=tuple(sorted({q.company for q in questions})),
        )
    except IndexConfigurationError as exc:
        print(f"\nretrieval preflight FAILED\n{exc}", file=sys.stderr)
        return 1
    print(f"index:     {manifest.collection} - {manifest.point_count} chunks, "
          f"{len(manifest.companies)} companies, built with {manifest.embedding_model}\n")

    deps = PipelineDeps(
        retriever=HybridRetriever(index=index, embedder=embedder),
        natural_provider=build_provider(natural.provider),
        program_provider=build_provider(program.provider),
        verifier_provider=build_provider(verifier.provider),
        natural_model=natural.model,
        program_model=program.model,
        verifier_model=verifier.model,
        document_id=None,
        company=None,
    )

    result = run_campaign(
        payload,
        arms,
        deps,
        run_id=args.resume,
        on_progress=print,
        extra_config={
            "split": args.split,
            "dataset": str(path),
            "include_ambiguous": args.include_ambiguous,
            "embedding_model": args.model,
            "collection": args.collection,
            "independence": note,
        },
    )

    print("\n" + "=" * 70)
    print(f"run artifact: {RUNS_ROOT / result.run_id}")
    print(f"completed {result.completed}, skipped {result.skipped}, failed {result.failed}")
    if result.stopped_early:
        print(f"STOPPED: {result.stop_reason}")
        print(f"resume:  {result.resume_command()}")
        return STOPPED_EARLY
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
