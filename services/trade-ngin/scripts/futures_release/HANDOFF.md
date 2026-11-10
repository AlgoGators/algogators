# Handoff: #180 (futures release) and #185 (production move), as of 2026-11-09

For whoever picks this up (Pranav, Hemdutt, Gannon, or Dom later). The step order is `README.md` in this folder; this
note says where things stand, what was measured, and what is still open. Nothing has been run on production for the
sitting yet. Steps 03 and 05 (part A) ran on production on 2026-10-30, as before.

## Where things stand

| Item | State |
|---|---|
| PR #206 (this branch): SIL fee $1.36, step 8a, new anchors, these notes | Draft, needs review and merge before `REF` is picked |
| PR #84, #85, #87 (raohemdutt) | Drafts; each merges at its step in the README order (#84 step 4, #87 step 5, #85 step 9) |
| PR #203 (stop the old futures DAG, #185) | Draft. **Do not merge before the #185 cutover morning**, then also to `prod` |
| Issue #202 (rename `new_algo_data` to `algo_data`, drop the old DB) | Assigned to Gannon-Stoner, after #185 |
| Operator for the sitting | Dom, from Windows + WSL (Ubuntu 24.04). No Mac needed |
| Sitting date | Not picked. Proposed: a Saturday, starting ~11:00 ET, after the 09:30 ET run. Takes 5 to 6 hours |

## What the local rehearsal measured (all on a scratch copy of production, never on production)

Production was copied read-only into a local Postgres 16 + TimescaleDB in WSL (`rehearsal/scratch-copy.sh`), with the
2026-11-04 Databento pull (data through 2026-11-03).

- Logins: the Vault `leadership` login has every right the sitting needs (47 ok, 0 missing; `rehearsal/check-login.sql`).
- Data steps at the old fee: part A2, hole fill (476 rows), part B (+9122), micros fill (8): all PASS, and all 7
  fingerprints equal the reference in `sql/expected_fingerprint.txt`.
- Live chain at $0.86 (the old fee): 385 runs, 0 failures, 9 identities PASS; matched the old live reference exactly.
- Live chain at $1.36: 385 runs (~21 s each, 2.2 h), 9 identities PASS. The book on 2026-10-23 is 519,535.86, $6.00 lower
  (12 SIL contracts x $0.50). No fill's quantity or price changes through 2026-10-23; on 2026-10-24 the old fee sold 1 SIL
  and the new one does not. That is the new `LIVE_REFERENCE` in `11_live_chain.sh`.
- Step 8a (`07b_sil_fee.sh`): metadata md5 37211534... becomes d642918b... (the new expected value); a second run changes
  nothing.

## Not tested; watch these at the sitting

- **Hypertables.** The scratch tables are plain tables. The fills have never run on a real TimescaleDB hypertable
  (production runs TimescaleDB 2.17.2+, the rehearsal 2.31). Read each step's output before going on.
- Backtests (step 13) were not rerun at $1.36; they have no reference numbers to match anyway.
- $1.36 applies to every chain date, also before the fee rose on 2026-04-01 (a few dollars at most).
- Chain speed from WSL against production is untimed. Last time ~28 s a run, so ~3 h; a DB login lasts 4 h. Check the
  first few runs.
- CME's 2026-11-02 fee schedule was not read. A different SIL fee means a rerun of the $1.36 chain on scratch.
- The typed confirmation of step 8a was not exercised (the test passed `CONFIRM`).

## Open asks, in order

1. Review and merge #206. Then `REF` = a SHA on `main` after that merge (history was rewritten 2026-11-04: use a fresh clone).
2. Accept $1.36, or check CME's 2026-11-02 schedule for SIL.
3. **Pranav:** confirm AlgoLens shows MGC and SIL correctly once they are stored (names, positions, and the switch from
   GC and SI on the listing dates), or say what it needs. Asked in #180.
4. Pick the sitting date. The sitting needs a fresh Databento pull (~$1).
5. Record the frozen tuning on #180 (`config_reference/portfolio.json`; note `fast_off_high_vol` is not in the code).

## Running the sitting from WSL (operator notes)

- DB logins come from Vault, never from chat or a file in git. Per sitting:
  `bash rehearsal/vault-session.sh login` once (8 h token), then `bash rehearsal/vault-session.sh creds` at the start,
  before step 13, and before step 15. Set `DB_HOST`/`DB_PORT`, or `DB_ENV_FILE=<.env with them>`, first. A login lives
  at most 4 hours from creation and renewing cannot pass that; scripts read the login once when they start, so a fresh
  login only reaches the next script started.
- WSL memory (~6 GB here): run `rehearsal/prebuild.sh <REF>` first (2 build jobs, tests off; GTest is not installed), and
  run step 15 in your own terminal with browsers and games closed. A low-memory kill stops the chain; resume, don't restart.
- QT stop (step 6): `QT_CATCHUP_ENABLED=0` in engine-rpc's `.env`, then recreate the container. The folder:
  `docker inspect engine-rpc --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'`. Stop after
  10:00 ET; restart before ~04:30 ET the next day. Confirm the stop yourself.
- Steps 7 (part B) and 14 (live reset) change production in ways that are slow to undo: get an explicit go first.

## Rehearsing again

All in `rehearsal/`, all refuse anything but a local DB whose name contains `scratch`:

| Script | Does |
|---|---|
| `scratch-setup.sh` | `sudo`, once: Postgres 16 + TimescaleDB on 127.0.0.1:5433, DB `release_scratch`, login in `~/.scratch-pg` |
| `scratch-copy.sh` | Read-only copy of production's futures_data, metadata, trading (+ empty backtest). Re-run replaces it |
| `rehearse-data.sh <pull folder>` | Steps 2, 3, 5, 7, 8, 8a on scratch, stops at the first failure |
| `prebuild.sh [ref]` | Builds the release worktree gently |
| `REF=<sha> chain.sh <tag>` | Live reset + full chain + identity check on scratch |
| `REF=<sha> chain-resume.sh <date>` | Resumes a killed chain from a date |
| `chain-state.sh` | Progress and free memory |
| `fee-compare.py <archive>` | Fill-by-fill diff of the scratch book against an archived one |

WSL stops the scratch server when it idles; start it with
`wsl.exe -d Ubuntu-24.04 -u root -- pg_ctlcluster 16 scratch start`, stop it with `sudo pg_ctlcluster 16 scratch stop`.

## #185 after the release

Not started beyond PR #203. Left to do: compare the old and new database for what the daily run reads, a dry run of a new
image from `main` against `new_algo_data` (no e-mail to the real list), then the cutover morning: merge #203 (and to
`prod`), switch the cron and services, and record the image commit and the cron's database line on #185.
