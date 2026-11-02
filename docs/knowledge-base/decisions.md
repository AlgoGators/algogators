# Decisions

Newest first. Each says what was decided, why, and where it was settled. A superseded decision
stays, marked as such. Issue and PR numbers are this repository's unless they name another.

## 2026-11-02 · trade-ngin lives here; the old repository is archived

**Decided.** The C++ engine (`AlgoGators/trade-ngin`) is `services/trade-ngin/`. Its history was
rebuilt for the import (one commit per merged PR, AI attribution lines removed) and the rebuilt
history went into this repository only. trade-ngin's own history was left as it was, so nobody's
old clone needs a reset ("option B"). Then:

- open PRs continued here as PRs from `trade-ngin-pr/<branch>`. The mergeable ones landed (#101,
  #102, #103, #104, #105, #107), each squash commit carrying `Moved-from: AlgoGators/trade-ngin#N`
  (`git log --grep Moved-from`), and the old PR closed with a link;
- open issues were transferred (65; comments, assignees and labels kept). Before the move, every
  bare `#N` in their text was rewritten to `AlgoGators/trade-ngin#N` so it still points at what it
  meant. The old-to-new table is on #90. Four that were done or superseded were closed instead
  (trade-ngin #100, #188, #189, #264);
- the regime PRs (#109 to #118) stay open here for Sivan; see "Regime detection" below;
- the `tradengin` Python package is built and published from here (`svc-trade-ngin.python.yml`).

**Why.** One repository for CI, deploys and issues; the move was the cheapest point to clean the
history. **Source:** #90 and its comments, #98, #120.

## 2026-11-02 · Branches: delete merged and closed PR branches, keep the rest

**Decided.** A branch whose PR is merged or closed, and that no open PR uses, is deleted (35 on
2026-11-02; each restorable from its PR page). A branch with no PR is kept: most are history
imported from data-ngin, AlgoLens and trade-ngin, and deleting one cannot be undone.

## 2026-11-02 · The issue tracker page is published from its own repository

**Decided.** `tools/issue-tracker/sync.py` builds the page; the public repository
`AlgoGators/issue-tracker` runs it hourly (and on Run workflow) and publishes
https://algogators.github.io/issue-tracker/. The order of work is the list in #156.

**Why.** This repository's Pages site serves algogators.com and cannot serve a second site. Every
repository the script reads is public, so the publishing repository needs no stored secret.
**Source:** #90, #192.

## 2026-11-02 · CI: prod deploys main's tested image; builds are cached

**Decided.**
- A push to `main` that touches trade-ngin builds and tests it, and pushes the engine image
  `ghcr.io/algogators/trade-ngin-engine:sha-<commit>` alongside the tests (#125).
- A push to `prod` (a fast-forward of `main`) deploys that image when main's quality run for the
  commit passed (waiting for it if it is still running), instead of building again. With no image,
  or a failed run, it builds and tests the commit itself (#123, #125).
- The C++ build caches vcpkg's packages as files and compiler output with ccache, links with mold,
  uses `-g1` and precompiled headers for the tests, and builds no tests in the image (#123). A warm
  build and test takes about 3 minutes (was 49).

**Rejected.** Parallel `ctest -j`: tests share fixed working-directory files (`test_config.json`,
`test_logs/`) and find repo files from the working directory, so concurrent runs would race.
ccache inside the Docker build: the GitHub cache backend does not keep BuildKit cache mounts.
Larger runners: an organisation billing setting, not taken.

## 2026-11-01 · Risk config schema 2 names its overlay covariance

**Decided.** The engine (from trade-ngin #257) refuses a `carver` risk module without
`overlay_covariance`. The QT books' `risk.json` on the production box name `"sample"`, the repo
template's value and the behaviour before the change. The box's `portfolio.json` files still lack
some newer template keys (`per_contract_rolls` and others) on purpose: switching those on changes
trading and is tracked in #180. **Source:** production logs, 2026-11-01; `config_template/`.

## 2026-10-30 · The daily book e-mail is the Python report only

**Decided.** The engine's book e-mail is built by the Python report (the AlgoLens design) in
`services/trade-ngin/services/rpc/algogators_rpc/services/report/`; the C++ e-mail code is deleted
and the hand-off to AlgoLens is reverted (trade-ngin #261, commit `167bfac4`). AlgoLens no longer
sends a report (#86). **Supersedes** trade-ngin #264 ("keep the engine's e-mail"). The recipient
list lives in the box's `email.json`, never in this repository.

## Standing · How the QT day runs (and why a missed day is not sent late)

The scheduler is described at the top of `services/trade-ngin/services/rpc/algogators_rpc/services/desk/catchup.py`
(contract C6, C7; rulings 18 and 29 as amended 2026-10-09; D-40). Model run about 07:00, desk
approval by 09:30, send at 09:30, fallback publish at 10:00. **A day that was published but never
e-mailed before it passed is not sent late**; the President is alerted instead. Do not "fix" this
by sending old days.

## Standing · Regime detection lives on its own branch

The regime work is on `trade-ngin-pr/regime_detection_updated` (34 commits ahead of `main` when it
was imported, last touched 2026-08-30), not on `main`. Sivan's fixes are PRs #109 to #118 against
it. #110, #113 and #117 are proposals that need HD's ruling. Merging regime into `main` is its own
project (#128, #148 to #151).

## Standing · Python package releases

`tradengin` on PyPI: push a tag `tradengin/vX.Y.Z` that matches `services/trade-ngin/pyproject.toml`;
`svc-trade-ngin.python.yml` builds Linux and Windows wheels for Python 3.10 to 3.13, tests each in a
clean interpreter and uploads with the `PYPI_API_TOKEN` secret from the `pypi` environment. 0.1.0
and 0.1.1 were released from trade-ngin (tags `vX.Y.Z`); the next release bumps the version.
**Source:** #191, `services/trade-ngin/python/packaging/README.md`.

## Standing · Merge and history rules

Squash by default; no AI attribution; a PR that moves stored numbers says so and names who
re-seeds. **Source:** #179.
