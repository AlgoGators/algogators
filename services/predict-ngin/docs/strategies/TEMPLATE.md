# {PREFIX}: {one-line title}

Last revised: {YYYY-MM-DD}

<!--
Report skeleton for a new study. Save it as docs/{PREFIX}_{SHORT_TITLE}.md: a topic name, never a date
(upper-case prefix, so the catalog's default glob docs/{PREFIX}_*.md links it). Keep `Last revised:` under the
title and update it whenever the content changes; later rounds update this doc in place (add a dated results
table or a History section) instead of creating a new dated copy. `python scripts/research/new_strategy.py` fills
it in for you. Delete these comments when done. Write the pre-registration BEFORE computing any test-period
number, and commit it (or log a PREREG row in research/trials.csv) so the order is provable.
-->

Research only: no orders, no keys. Ledger: `scripts/research/ledger_entries/{prefix}.yaml`.
Code: `scripts/research/{prefix}_*.py`. Data: `data/vendor/{prefix}/`.

## Question

What edge is claimed, by whom (brief, paper, backlog item), and why it should exist (who is on the other side,
why they accept losing). Link the brief in `docs/research_briefs/`.

**Check first:** `docs/strategies/REJECTED.md` and `docs/RESEARCH_LEDGER.md`. If a ledger entry already
rejected this idea, say which `retest_if` condition now holds; otherwise stop.

## Pre-registration (written {YYYY-MM-DD}, before any test-period number was computed)

- **Universe** (point-in-time; no ex-post volume or outcome filters):
- **Train / test split** (dates):
- **Signal and entry rule** (exact; parameters fixed on train only):
- **Fill model** (causal: Fa / Fb / Fc from `src/data/fills.py`, or a stated alternative; no fills at the print):
- **Fees** (point-in-time: `scripts/research/feeaudit_common.py`; per-order rounding):
- **Sizing and capital** ($1k / $5k / $10k; per-event and per-category caps):
- **Kill rule / pass bar** (e.g. Fb > +1c per contract with CI > 0 over >= 300 fills; average deployed >= $1k):
- **Trials budget** (how many variants will be tried; every one goes in `research/trials.csv`):

## Data

Sources, date ranges, row counts, known gaps (vintages, timestamps, survivorship). Note any paid data and its cost.

## Results

### Edge per contract (train, then test)

### Portfolio and capacity at $1k / $5k / $10k

Average deployed, $/yr, top-5 event share, DSR with the true trial count.

### Look-ahead and robustness checks

Point-in-time universe, timestamp audit, fill-model sensitivity, cost sensitivity, leave-one-event-out.

## Limitations

## Trials

Number of variants and where they are logged (`research/trials.csv`, strategy = `{prefix}`).

## Verdict

One of `rejected` / `invalid` / `capacity_capped` / `unproven` / `candidate` / `live`, with the one-line reason,
the next test (for `unproven`), and the "re-test only if" condition (for `rejected`). The same text goes into
the ledger YAML.
