# Knowledge base

What a person or an agent new to this repository needs before changing anything: the decisions
already taken and why, how production runs, where things live, who owns what, and the traps that
have already cost someone time. Start here, then read the page for your task.

| Page | Read it when |
|---|---|
| [decisions/](decisions/README.md) | before proposing a change to something that looks odd: it is often a decision |
| [operations.md](operations.md) | before touching production, a deploy, the daily e-mail or the database |
| [where-things-live.md](where-things-live.md) | looking for code, an issue, a workflow, a config or a page |
| [people.md](people.md) | deciding who to ask, or who works on an area |
| [gotchas.md](gotchas.md) | before scripting against GitHub, CI or the box, and when something fails oddly |

One page per area of the repository, for working inside it:

| Area | Covers |
|---|---|
| [trade-ngin](areas/trade-ngin.md) | the engine: books, strategies, risk, data in and out, building and testing |
| [data-ngin](areas/data-ngin.md) | the market-data pipelines (Airflow), their schedule, tables and deploy |
| [AlgoLens](areas/algolens.md) | the web app and research-api: the QT desk, auth, how it talks to the engine |
| [website](areas/website.md) | algogators.com: content, build, Pages and releases |
| [CI and deploys](areas/ci-and-deploys.md) | the workflow system, path filters, releases, `prod`, security scans |
| [libraries and research](areas/libraries-and-research.md) | algosystem, research-core, predict-ngin, algoterminal |
| [platform](areas/platform.md) | contracts, configs, the database package, the workspaces and `just` recipes |

## Rules that are never optional

- **No secrets, hostnames, IP addresses, e-mail addresses or licensed data in this repository, its
  issues or its pull requests.** It is public. Say where a value lives, never the value. IvyDB and
  Cboe data are licensed: only aggregates may appear anywhere public (#32).
- **No AI attribution** in commit messages or PR text: no AI `Co-authored-by`, no `Claude-Session:`,
  no `claude.ai/code` link, no "Generated with" footer (#179).
- **Squash merges**, PR title with `(#n)` as the subject (#179).
- **Nothing touches production between 09:00 and 10:15 New York time**: the 09:30 send and the
  10:00 cutoff run then. `services/trade-ngin/deploy/deploy.sh` waits it out by itself; a person or
  an agent must too.
- **A PR that moves stored numbers says so** and names who re-seeds the stored history (#179).

## Keeping it true

These pages are only useful while they are right.

- Every entry carries a date and a source: a PR, an issue, a commit or a file. Check the source
  before relying on an entry; the code wins when they disagree, and then fix the entry.
- Change a page in the same PR as the change that makes it wrong.
- Each area page starts with a `LAST UPDATED` date and the commit of `main` it was checked
  against. When you check or edit a page, update both. To see what changed since, run
  `git log --oneline <commit>..main -- <the area's paths>`.
- Add a decision record to `decisions/` (copy `decisions/template.md`) when a PR or an issue
  settles a question someone will ask again. Never rewrite one: a new record supersedes it.
- Keep pages short. Link to the issue, PR or file that has the detail instead of copying it.
