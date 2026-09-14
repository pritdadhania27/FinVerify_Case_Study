"""Build and maintain FinVerify-IND (spec Module 26, decisions D22 / D23).

    generate -> audit -> export -> [human validates] -> import -> split -> manifest

**Every candidate answer is mined from an extracted table cell, never composed.**
The generator reads chunked tables, finds rows whose label matches the metric
lexicon, and takes the figure that is actually printed there together with its
page and its scale banner. It cannot produce a number the document does not
contain, because it has no path to one.

**And that still does not make it gold.** Every candidate is written PENDING.
Mining the right cell is not the same as the cell being the right answer to the
question as phrased - the row could be the standalone rather than the
consolidated figure, the prior year rather than the current one, or one of two
line items whose labels differ by a word. That judgment is spec 16's human
verification, and no generator quality substitutes for it. `evaluation.dataset`
refuses to hand a PENDING question to an evaluation at all.

**The definition is pinned in the question text** (D22). "Return on equity" split
three ways across the channels in RX-007 because all three definitions are
standard. Main-set questions therefore name the definition they mean; the
ambiguity subset deliberately does not, and is scored separately.

Usage:
    python scripts/build_finverify_ind.py generate --limit 200
    python scripts/build_finverify_ind.py audit
    python scripts/build_finverify_ind.py export --out datasets/finverify_ind/worksheet.csv
    python scripts/build_finverify_ind.py import --csv worksheet.csv --validator "Name"
    python scripts/build_finverify_ind.py split --seed 20260826
    python scripts/build_finverify_ind.py manifest
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._console import use_utf8  # noqa: E402

use_utf8()

from backend.core.paths import project_path  # noqa: E402
from backend.agents.metrics_lexicon import DERIVED, LEXICON
from backend.documents.facts import facts_from_chunks
from backend.documents.statement_basis import CONSOLIDATED, STANDALONE, basis_for
from evaluation.dataset import (
    DATASET_ROOT,
    Dataset,
    DatasetQuestion,
    GoldAnswer,
    load_dataset,
    manifest,
    stratify,
)
from evaluation.validation import (
    export_for_validation,
    import_validations,
    intra_annotator_agreement,
    select_double_pass_subset,
)

DATASET_PATH = DATASET_ROOT / "finverify_ind_v1.json"
PROCESSED = project_path("documents/processed")
REGISTRY = project_path("documents/registry.json")

# A pipe-table row: "| label | 3,956 | 3,865 |"
_ROW = re.compile(r"^\|(?P<cells>.+)\|\s*$")
_NUMERIC = re.compile(r"^[(\-−]?[\d,][\d,.\s]*\)?$")

# Definitions pinned into main-set questions (D22). Each names the reading the
# question intends, so a defensible alternative reading is not scored as a
# hallucination. Only metrics that genuinely admit more than one reading need an
# entry; a lookup of a printed line item does not.
PINNED_DEFINITIONS = {
    "total equity": "total equity as reported on the consolidated balance sheet",
    "revenue from operations": "revenue from operations, excluding other income",
    "profit for the year": "profit for the year attributable to owners of the company",
    "return on equity": "profit for the year divided by CLOSING total equity",
    "return on assets": "profit for the year divided by CLOSING total assets",
    "net profit margin": "profit for the year divided by revenue from operations",
    "current ratio": "total current assets divided by total current liabilities",
    "goodwill as a share of total assets": (
        "goodwill divided by total assets, expressed as a percentage"
    ),
    # Contested in practice - gross vs net debt, with or without lease
    # liabilities - so the definition names exactly which line items to use.
    # Left unpinned it would belong in the ambiguity subset, and the audit
    # correctly refused it as a main-set question until this was written.
    "debt to equity ratio": (
        "borrowings divided by total equity, both as reported on the "
        "consolidated balance sheet, excluding lease liabilities"
    ),
    # Added with the sector vocabulary (RX-012). Banks report deposits and
    # advances across several schedules; the question means the consolidated
    # balance-sheet total, not a single schedule's line.
    "deposits": "total deposits as reported on the consolidated balance sheet",
    "advances": "total advances as reported on the consolidated balance sheet",
    "borrowings": "total borrowings as reported on the consolidated balance sheet",
    "investments": "total investments as reported on the consolidated balance sheet",
    "inventories": "total inventories as reported on the consolidated balance sheet",
    "total liabilities": "total liabilities as reported on the consolidated balance sheet",
}

# The subset that deliberately does NOT pin a definition, so the detector's
# false-positive rate on definitional ambiguity can be measured (D22).
AMBIGUITY_METRICS = ("return on equity", "return on assets", "net profit margin")

_ALIASES: dict[str, str] = {}
for _entry in LEXICON:
    _ALIASES[_entry.canonical.lower()] = _entry.canonical
    for _alias in _entry.aliases:
        _ALIASES[_alias.lower()] = _entry.canonical


def _load_chunks(document_id: str) -> list[dict]:
    path = PROCESSED / f"{document_id}.chunks.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        if record.get("chunk_id"):
            out.append(record)
    return out


def _registry() -> list[dict]:
    if not REGISTRY.exists():
        return []
    documents = json.loads(REGISTRY.read_text(encoding="utf-8")).get("documents", [])
    return list(documents.values()) if isinstance(documents, dict) else list(documents)


def _match_metric(label: str) -> str | None:
    """Canonical metric named by a table row label, if any.

    Requires the label to be *mostly* the metric name rather than merely to
    contain it. "Trade payables" matches; "Trade payables ageing schedule -
    disputed dues" does not, and must not, because the figure beside it is a
    different number answering a different question.
    """
    cleaned = re.sub(r"[^a-z0-9,\s\-]", " ", label.lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None
    for alias, canonical in _ALIASES.items():
        if cleaned == alias or cleaned.startswith(alias + " "):
            # Reject a label that carries much more than the metric name.
            if len(cleaned) <= len(alias) + 12:
                return canonical
    return None


def _cells(line: str) -> list[str]:
    match = _ROW.match(line.strip())
    if not match:
        return []
    return [c.strip() for c in match.group("cells").split("|")]


def mine_facts(chunks: list[dict]):
    """Delegate to Module 4 rather than re-implementing table mining.

    An earlier version of this script had its own row parser that took the first
    numeric cell in a row. That is the current year by convention and the prior
    year the moment a table leads with its comparative, and it collapsed a
    merged "153,670  146,767" cell to a single silent figure. Module 4 handles
    both, labels the column with its year where the header states one, and says
    so when it cannot - and having one implementation means a fix there reaches
    the dataset instead of only the pipeline.
    """
    return facts_from_chunks(chunks)


def _unit_for(fact) -> str:
    return fact.unit if fact.unit != "unit" else ""


def _evidence(facts) -> tuple[dict, ...]:
    """One group per distinct metric, with every page it was found on as an
    alternative. Mirrors the retrieval gold format, and encodes the lesson from
    the retrieval gold audit: a figure stated on four pages must not penalise a
    retriever that found it on the second."""
    by_metric: dict[str, list] = defaultdict(list)
    for fact in facts:
        by_metric[fact.metric].append(fact)
    groups = []
    for metric, hits in by_metric.items():
        spans, seen = [], set()
        for hit in hits:
            key = (hit.page, str(hit.value.amount))
            if key in seen:
                continue
            seen.add(key)
            spans.append(
                {
                    "page": hit.page,
                    "anchors": [hit.row_label[:60].strip(), str(hit.value.amount)],
                }
            )
        groups.append({"group_id": metric.replace(" ", "_"), "any_of": spans})
    return tuple(groups)


def _stable_qid(*parts: str) -> str:
    """A question id derived from what the question IS, not from its position.

    The counter this replaced renumbered every question whenever the generator
    ran, so a single regeneration silently invalidated every recorded human
    verdict - the one input this project cannot re-manufacture. Keying on
    content means a question keeps its id across regenerations, and a question
    that disappears takes its id with it instead of handing it to a different
    question.
    """
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"FI{digest[:8]}"


def _restate_basis(text: str, basis: str) -> str:
    """The same sentence, naming the basis the figure actually came from.

    The definitions are written for consolidated statements because that is
    what the questions were assumed to use. A question answered from the
    standalone section has to SAY standalone: the alternative is a question
    whose wording contradicts its own evidence, which is the defect this whole
    change exists to remove, re-introduced one layer up.
    """
    if basis == CONSOLIDATED:
        return text
    return re.sub(r"\bconsolidated\b", "standalone", text, flags=re.IGNORECASE)


def _honest_definition(definition: str, metric: str, row_label: str) -> str:
    """The definition, minus any claim the source row does not support.

    Several pinned definitions say "total X as reported on the consolidated
    balance sheet". Under Ind AS that total is frequently NOT PRINTED: the
    balance sheet splits borrowings into current and non-current and gives
    no sum. So the honest answer to "total borrowings" is an addition rather
    than a lookup, and the figure mined from the `Borrowings` row is one of
    the two parts. Reliance p110 reports 2,22,712 non-current and 1,01,910
    current; the question asked for the total and the candidate offered
    2,22,712, which a validator correctly rejected.

    When the definition claims a total and the row it was read from does
    not, the definition names the line item instead. The question becomes
    answerable exactly as worded - the only kind of question a gold label
    can honestly be attached to.
    """
    if not definition.lower().startswith("total "):
        return definition
    if "total" in row_label.lower():
        return definition
    _, _, rest = definition.partition(" ")
    remainder = rest[len(metric):].lstrip() if rest.lower().startswith(metric) else rest
    return f"the {row_label.strip()!r} line item {remainder}".strip()


def _by_basis(hits: list, basis_of: dict[int, str]) -> tuple[str, list] | None:
    """The best-supported basis for these facts, and the facts supporting it.

    Preference is consolidated, then standalone, then nothing. `None` means
    every page carrying this figure sits outside both sets of statements - a
    front-of-report highlights table, typically - and no truthful question can
    be asked of it, because we cannot say which basis it reports. HDFC Bank's
    "Summary of Financial Performance" states advances as 1,600,585.9 where the
    consolidated balance sheet on p412 says 1,661,949.29; a question naming
    either basis would be wrong about one of them.
    """
    for basis in (CONSOLIDATED, STANDALONE):
        chosen = [hit for hit in hits if basis_of.get(hit.page) == basis]
        if chosen:
            return basis, chosen
    return None


def generate(limit: int, *, seed: int = 20260826) -> Dataset:
    """Mine candidates from every processed document.

    Deliberately produces MORE candidates than the 150 target: validation will
    reject some, and a dataset that arrives exactly at its target only if every
    candidate survives is a dataset that will quietly shrink below its power
    calculation.
    """
    documents = _registry()
    candidates: list[DatasetQuestion] = []
    counter = 0
    skipped_documents: list[str] = []
    unplaceable: dict[str, int] = {}

    for document in documents:
        chunks = _load_chunks(document["document_id"])
        if not chunks:
            skipped_documents.append(document["filename"])
            continue
        facts = mine_facts(chunks)
        company = document.get("company", "")
        year = document.get("fiscal_year", "")
        # Which set of statements each page belongs to. A filing states most
        # metrics twice and the two are different numbers; without this the
        # generator picks whichever page it meets first, which for Sun Pharma
        # is the standalone section and for Tata Motors is a standalone note
        # that prints the one row label the consolidated balance sheet lacks.
        basis_of = basis_for(project_path(document["path"]))
        dropped_unplaceable = 0

        # Grouped by (metric, year), not by metric alone. A filing prints two
        # years side by side, so "total equity" is two different answers to two
        # different questions - and asking without naming the year is asking an
        # ambiguous question, which D22 says belongs in the ambiguity subset
        # rather than in the main set by accident.
        by_metric_year: dict[tuple[str, str | None], list] = defaultdict(list)
        by_metric: dict[str, list] = defaultdict(list)
        for fact in facts:
            by_metric[fact.metric].append(fact)
            if fact.year_is_stated:
                by_metric_year[(fact.metric, fact.year)].append(fact)

        # --- lookups: one per (metric, stated year) ------------------------
        for (metric, fact_year), hits in sorted(
            by_metric_year.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")
        ):
            selected = _by_basis(hits, basis_of)
            if selected is None:
                # Every page carrying this figure is outside both sets of
                # statements. Dropped rather than asked on a guessed basis.
                dropped_unplaceable += 1
                continue
            statement_basis, hits = selected
            counter += 1
            head = hits[0]
            definition = _honest_definition(
                _restate_basis(
                    PINNED_DEFINITIONS.get(
                        metric,
                        f"{metric} as reported in the consolidated financial statements",
                    ),
                    statement_basis,
                ),
                metric,
                head.row_label,
            )
            candidates.append(
                DatasetQuestion(
                    qid=_stable_qid(document["document_id"], metric, fact_year or ""),
                    question=(
                        f"For {company}, as reported for the year ended "
                        f"March 31, {fact_year}, what were {metric} "
                        f"({definition})?"
                    ),
                    company=company,
                    fiscal_year=year,
                    document_id=document["document_id"],
                    answer=GoldAnswer(
                        text=str(head.value.amount),
                        unit=_unit_for(head),
                        source_page=head.page,
                        source_note=(
                            f"row {head.row_label[:60]!r}, column "
                            f"{head.column_label!r} ({head.citation})"
                        ),
                    ),
                    definition=definition,
                    question_type="lookup",
                    difficulty="easy" if len(hits) > 1 else "medium",
                    evidence=_evidence(hits),
                    provenance={
                        "generated_by": "scripts/build_finverify_ind.py",
                        "generated_on": datetime.now(UTC).date().isoformat(),
                        "chunk_id": head.chunk_id,
                        # Recorded so error analysis can check mechanically
                        # whether question understanding found the right metric
                        # - D19's common-mode failure path.
                        "metric": metric,
                        "reported_year": fact_year,
                        # Which set of statements the answer was read from, so
                        # a later audit never has to re-derive it from the page
                        # number and a mismatch is checkable mechanically.
                        "statement_basis": statement_basis,
                        "year_source": head.year_source,
                        "warnings": list(head.warnings),
                        "candidate_only": True,
                    },
                )
            )

        # --- derived metrics: the computed and multi-hop strata -------------
        for derived in DERIVED:
            if not all(component in by_metric for component in derived.components):
                continue
            # Every component must come from the SAME set of statements, and a
            # ratio mixing the two is worse than a wrong lookup: consolidated
            # profit over standalone equity is a number with no referent at all.
            component_facts, bases = [], set()
            for component in derived.components:
                selected = _by_basis(by_metric[component], basis_of)
                if selected is None:
                    break
                component_basis, component_hits = selected
                bases.add(component_basis)
                component_facts.append(component_hits[0])
            if len(component_facts) != len(derived.components) or len(bases) != 1:
                dropped_unplaceable += 1
                continue
            statement_basis = bases.pop()
            counter += 1
            ambiguous = derived.canonical in AMBIGUITY_METRICS
            definition = "" if ambiguous else _restate_basis(
                PINNED_DEFINITIONS.get(derived.canonical, ""), statement_basis
            )
            question = (
                f"For {company}, in the {year} {statement_basis} financial "
                f"statements, what was {derived.canonical}"
                + (f" ({definition})?" if definition else "?")
            )
            candidates.append(
                DatasetQuestion(
                    qid=_stable_qid(document["document_id"], derived.canonical),
                    question=question,
                    company=company,
                    fiscal_year=year,
                    document_id=document["document_id"],
                    # No candidate answer: computing it here would put an
                    # unvalidated arithmetic result into the gold field, and a
                    # validator is far more likely to wave through a number that
                    # is already filled in than to notice one that is missing.
                    answer=None,
                    definition=definition,
                    ambiguous=ambiguous,
                    question_type="multi_hop" if len(derived.components) > 1 else "computed",
                    difficulty="hard",
                    evidence=_evidence(component_facts),
                    provenance={
                        "generated_by": "scripts/build_finverify_ind.py",
                        "generated_on": datetime.now(UTC).date().isoformat(),
                        "metric": derived.canonical,
                        "statement_basis": statement_basis,
                        "components": list(derived.components),
                        "operation": derived.operation,
                        "candidate_only": True,
                        "answer_left_blank": (
                            "a computed gold answer must be worked out and signed "
                            "for by the validator, not pre-filled by the generator"
                        ),
                    },
                )
            )

        if dropped_unplaceable:
            unplaceable[company] = dropped_unplaceable

    random.Random(seed).shuffle(candidates)
    notes = [
        (
            "Every question is a CANDIDATE. None is gold until a human validates "
            "it (spec 16); evaluation.dataset refuses to serve PENDING questions."
        ),
        "Lookup answers are mined from a printed table cell, never composed.",
        (
            "Derived-metric questions carry NO candidate answer on purpose: a "
            "pre-filled arithmetic result invites a validator to wave it through."
        ),
        (
            "Definitions are pinned per D22; the ambiguity subset "
            f"({', '.join(AMBIGUITY_METRICS)}) deliberately leaves them unpinned "
            "and is scored separately."
        ),
    ]
    notes.append(
        "Every question names the set of statements its answer was read from, "
        "and the answer is read from that section (RX-026, D42). A filing "
        "states most metrics twice - Tata Motors' total borrowings are "
        "13,771.04 crore standalone and 98,500.09 crore consolidated - so a "
        "question that names one basis and is answered from the other is "
        "simply wrong, without anything raising."
    )
    if unplaceable:
        notes.append(
            "NOT ASKED, because every page carrying the figure sits outside "
            "both sets of statements (a front-of-report highlights table, "
            "typically) and no basis could be stated truthfully: "
            + ", ".join(f"{company} {count}" for company, count in sorted(unplaceable.items()))
        )
    if skipped_documents:
        notes.append(
            "NOT MINED (no extracted chunks on disk, so no candidates exist for "
            f"them): {', '.join(skipped_documents)}"
        )  # noqa: ISC004
    return Dataset(
        set_id="finverify-ind-v1",
        version="0.1.0-candidates",
        created_on=datetime.now(UTC).date().isoformat(),
        notes=tuple(notes),
        questions=tuple(candidates[:limit]),
    )


def assign_splits(dataset: Dataset, *, seed: int = 20260826) -> Dataset:
    """Stratified train / validation / test split, seeded and reproducible.

    Stratified on (question_type, ambiguous) so the test split cannot end up
    without a multi-hop question by chance - which on 150 questions is not a
    remote possibility. The seed is recorded so the split is a property of the
    dataset rather than of whoever ran the script.

    The proportions favour validation because that is where the operating
    threshold and every calibration decision are fixed, and the test split is
    evaluated exactly once.
    """
    rng = random.Random(seed)
    buckets: dict[tuple, list[DatasetQuestion]] = defaultdict(list)
    for question in dataset.questions:
        buckets[(question.question_type, question.ambiguous)].append(question)

    updated: list[DatasetQuestion] = []
    for _, group in sorted(buckets.items(), key=lambda kv: str(kv[0])):
        ordered = sorted(group, key=lambda q: q.qid)
        rng.shuffle(ordered)
        n = len(ordered)
        n_train = round(n * 0.30)
        n_validation = round(n * 0.30)
        for index, question in enumerate(ordered):
            split = (
                "train" if index < n_train
                else "validation" if index < n_train + n_validation
                else "test"
            )
            updated.append(_with_split(question, split))
    return Dataset(
        set_id=dataset.set_id,
        version=dataset.version,
        created_on=dataset.created_on,
        notes=dataset.notes
        + (f"splits assigned with seed {seed}, stratified by (question_type, ambiguous)",),
        questions=tuple(sorted(updated, key=lambda q: q.qid)),
    )


def _with_split(question: DatasetQuestion, split: str) -> DatasetQuestion:
    return DatasetQuestion(
        qid=question.qid,
        question=question.question,
        company=question.company,
        fiscal_year=question.fiscal_year,
        document_id=question.document_id,
        answer=question.answer,
        definition=question.definition,
        ambiguous=question.ambiguous,
        question_type=question.question_type,
        difficulty=question.difficulty,
        evidence=question.evidence,
        validation=question.validation,
        split=split,
        provenance=question.provenance,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_generate = sub.add_parser("generate", help="mine candidates from processed documents")
    p_generate.add_argument("--limit", type=int, default=400)
    p_generate.add_argument("--seed", type=int, default=20260826)
    p_generate.add_argument("--out", default=str(DATASET_PATH))

    sub.add_parser("audit", help="report malformed records and stratum counts")

    p_export = sub.add_parser("export", help="write the human validation worksheet")
    p_export.add_argument("--out", default=str(DATASET_ROOT / "worksheet.csv"))
    p_export.add_argument("--pass", dest="pass_number", type=int, default=1)
    p_export.add_argument("--double-pass-fraction", type=float, default=0.2)

    p_import = sub.add_parser("import", help="apply a completed worksheet")
    p_import.add_argument("--csv", required=True)
    p_import.add_argument("--validator", required=True)
    p_import.add_argument("--pass", dest="pass_number", type=int, default=1)

    p_split = sub.add_parser("split", help="assign train/validation/test")
    p_split.add_argument("--seed", type=int, default=20260826)

    sub.add_parser("manifest", help="record the dataset SHA-256")

    args = parser.parse_args()
    path = Path(getattr(args, "out", DATASET_PATH) or DATASET_PATH)

    if args.command == "generate":
        dataset = generate(args.limit, seed=args.seed)
        dataset.write(path)
        print(f"{len(dataset)} CANDIDATE questions written to {path}")
        print("  none of them is gold: every record is PENDING human validation")
        for note in dataset.notes:
            print(f"  - {note}")
        print("\nstrata:")
        for key, count in stratify(dataset.questions).items():
            print(f"  {key}: {count}")
        return 0

    if not DATASET_PATH.exists():
        print(f"no dataset at {DATASET_PATH}; run `generate` first", file=sys.stderr)
        return 1
    dataset = load_dataset(DATASET_PATH)

    if args.command == "audit":
        problems = dataset.audit()
        print(f"{len(dataset)} questions, {len(dataset.validated())} validated, "
              f"{len(dataset.pending())} pending")
        awaiting = sum(1 for q in dataset.questions if q.awaiting_computation)
        print(f"{len(problems)} record(s) with problems")
        if awaiting:
            print(
                f"{awaiting} derived-metric question(s) awaiting the validator's "
                "computed answer - an intended state, not a defect"
            )
        for qid, issues in list(problems.items())[:40]:
            print(f"  {qid}: {'; '.join(issues)}")
        print("\nstrata:")
        for key, count in stratify(dataset.questions).items():
            print(f"  {key}: {count}")
        print("\nvalidation:", json.dumps(intra_annotator_agreement(dataset.questions), indent=2))
        return 1 if problems else 0

    if args.command == "export":
        questions = dataset.questions
        if args.pass_number > 1:
            questions = select_double_pass_subset(
                dataset.validated(), fraction=args.double_pass_fraction
            )
            print(f"second pass: {len(questions)} of {len(dataset.validated())} validated "
                  "questions, selected by seed (never by which ones the system got wrong)")
        count = export_for_validation(questions, path)
        print(f"{count} rows written to {path}")
        print("  fill `verdict` with validated | rejected | needs_review")
        print("  leave `corrected_answer` blank if the candidate answer is right")
        return 0

    if args.command == "import":
        updated, report = import_validations(
            dataset, args.csv, validator=args.validator, pass_number=args.pass_number
        )
        updated.write(DATASET_PATH)
        print(json.dumps(report.as_dict(), indent=2))
        print(f"\n{len(updated.validated())} of {len(updated)} questions are now usable as gold")
        if report.corrections:
            print(f"{len(report.corrections)} answer(s) corrected; the generated value is "
                  "preserved in provenance.generated_answer")
        if report.anchors_invalidated:
            print()
            print(f"  {len(report.anchors_invalidated)} question(s) had their EVIDENCE "
                  "ANCHORS dropped as stale:")
            print(f"    {', '.join(report.anchors_invalidated)}")
            print("  They cited the figure the correction rejected, so they no longer")
            print("  locate this answer. The old anchors are in")
            print("  provenance.superseded_evidence and need re-deriving before")
            print("  anything reads evidence for these questions again - the oracle")
            print("  arm, evidence_was_retrieved, the H2 stratifier and the retrieval")
            print("  metrics all resolve through them (RX-042).")
        return 0

    if args.command == "split":
        updated = assign_splits(dataset, seed=args.seed)
        updated.write(DATASET_PATH)
        for name in ("train", "validation", "test"):
            print(f"{name}: {len(updated.split(name))}")
        return 0

    if args.command == "manifest":
        record = manifest(DATASET_PATH)
        (DATASET_ROOT / "manifest.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8"
        )
        print(json.dumps(record, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
