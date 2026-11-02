# Gotchas

Traps that have already cost time here, with the fix. Add one when something surprises you.

## Engine build and tests

- **The mode-branch guard test fails on any new clock or environment read in the library.**
  `tests/live/test_mode_branch_guard.cpp` lists every line in `src/` and `include/` that reads
  `now()`, `getenv`, `localtime` or the date flag (`kLibrarySiteLines`). A new one fails
  `ModeBranchGuard.TheLibraryReadsTheModeOnlyAtTheListedLines` until it is listed with what it is
  for. If it decides anything on a live run, name the test that executes both of its arms.
- **Some tests read source files through `__FILE__`.** Do not set `CCACHE_BASEDIR` (it makes
  `__FILE__` relative and breaks them).
- **`@loader_path` is macOS-only.** The build rpath is set only on Apple; mold reads it as a
  response file on Linux.
- **Tests write fixed files in the working directory** (`test_config.json`, `test_logs/`), so the
  suite must run serially.
- **The engine refuses a config it does not understand**, at load, on the box. A schema change in a
  PR needs the box's configs updated in the same rollout ([decision 0006](decisions/0006-risk-config-schema-2.md)).

## GitHub

- **`gh pr merge --admin` merges without waiting for checks.** Wait until every check is green.
- **`gh issue transfer N <dest>` reads N in the current directory's repository** unless you pass
  `-R <source>`.
- **Transferring an issue rewrites references to it elsewhere, but leaves bare `#N` in its own text
  alone**, which then points at the destination's `#N`. Qualify them first
  (`AlgoGators/<old-repo>#N`).
- **GitHub's search counts lag** for minutes after a bulk change; count with `gh issue list` instead.
- **`gh api --paginate` prints one JSON array per page.** Use `--slurp`, or a `--jq` filter per
  item; do not join the pages with a text replace (it corrupts bodies containing `][`).

## Windows (Git Bash) tooling

- **Git Bash rewrites arguments that look like paths**: `origin/main:path` and `docker -v` mounts.
  Prefix the command with `MSYS_NO_PATHCONV=1`. In `PATH`, write `/c/...`, not `C:/...`.
- **Python's `subprocess` decodes with the Windows code page by default.** Pass `encoding="utf-8"`
  to every call that reads or writes text from `gh` or `git`; on 2026-11-02 the default garbled
  four issue texts it read and wrote back. Write files with `encoding="utf-8", newline=""`.

## Production

- **`_deploy-ssh.yml` exports `COMPOSE_FILE`** for the other services; deploy.sh unsets it, since
  engine-rpc's compose file has the default name.
- **`prod` is shared** by three deploys. Fast-forward it to a commit of `main`; never push other
  work to it.
- **A missed e-mail day is never sent late by design** ([decision 0002](decisions/0002-qt-day-and-missed-sends.md)). Look for `missed_send` and
  `send_failed` in the logs before assuming the scheduler is broken.
