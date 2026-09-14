# Retrieval evaluation gold — which set is current

Two generations of gold live in this directory and **they are not
interchangeable**. Measuring against the superseded one produces numbers that
look plausible and grade retrieval against evidence the dataset no longer
claims.

The test is whether a set's `source_qid`s still resolve to questions in
`datasets/finverify_ind/finverify_ind_v1.json`. D42 replaced sequential ids
(`FI0250`) with content-derived ones (`FI222c2fc4`), so a set built before D42
points at questions that no longer exist.

| File | Company | Questions | `source_qid`s that resolve | Status |
|---|---|---:|---:|---|
| `40f73920_retrieval_v1.json` | HDFC Bank | 25 | **25/25** | **CURRENT** |
| `cca3bdde_retrieval_v1.json` | Sun Pharmaceutical | 25 | **25/25** | **CURRENT** |
| `d8e3739d_retrieval_v1.json` | Reliance Industries | 25 | **25/25** | **CURRENT** |
| `db02424e_retrieval_v1.json` | Tata Motors | 25 | **25/25** | **CURRENT** |
| `40f73920_retrieval_recent.json` | HDFC Bank | 13 | 0/13 | SUPERSEDED |
| `cca3bdde_retrieval_recent.json` | Sun Pharmaceutical | 18 | 0/18 | SUPERSEDED |
| `d8e3739d_retrieval_recent.json` | Reliance Industries | 16 | 0/16 | SUPERSEDED |
| `db02424e_retrieval_recent.json` | Tata Motors | 16 | 0/16 | SUPERSEDED |
| `infosys_fy24_v1.json` | Infosys | 22 | n/a — hand-built | SPECIAL, see below |

## The `*_recent` sets are superseded, and kept only as history

They were built on 2026-08-29 against the pre-D42 dataset, whose answers were
partly read from the wrong financial statements (RX-026) and from columns
labelled a year early (RX-029). **Not one of their source questions survives.**
Their evidence pages are the defective provenance those findings corrected — for
example, Tata's "finance cost" evidence points at a defined-benefit-obligation
note, whose `Interest expense` row is not the consolidated finance cost line.

They are preserved because `RX-020`'s recent-year control (0.492) was measured on
them and the number should stay reproducible. **Do not use them to measure
anything now.** RX-028 onwards used the `*_v1` sets.

## Infosys is constructed differently from the other four

`infosys_fy24_v1.json` is hand-built (2026-08-23) rather than generated from
FinVerify-IND — it carries no `source_qid` at all. Every other set is generated
by `scripts/build_retrieval_gold.py` from validated answer gold.

This matters when reading the transfer gap. Infosys scores 0.909 while the
generated sets score 0.28–0.56, and **part of that difference may be gold
construction rather than document difficulty**. RX-028 recorded that it could not
explain the gap; a difference in how the two kinds of gold were built is a
candidate explanation that has not been tested. Any claim of the form "retrieval
transfers poorly from the development document" has to survive that objection.
