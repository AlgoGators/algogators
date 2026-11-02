# Knowledge base

What a person or an agent new to this repository needs before changing anything: the decisions
already taken and why, how production runs, where things live, who owns what, and the traps that
have already cost someone time. Start here, then read the page for your task.

| Page | Read it when |
|---|---|
| [decisions.md](decisions.md) | before proposing a change to something that looks odd: it is often a decision |
| [operations.md](operations.md) | before touching production, a deploy, the daily e-mail or the database |
| [where-things-live.md](where-things-live.md) | looking for code, an issue, a workflow, a config or a page |
| [people.md](people.md) | deciding who to ask, who rules on what, or who owns an area |
| [gotchas.md](gotchas.md) | before scripting against GitHub, CI or the box, and when something fails oddly |

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
- Add a decision to `decisions.md` when a PR or an issue settles a question someone will ask again.
  Do not rewrite an old decision: add a new dated one that supersedes it and say so in the old one.
- Keep pages short. Link to the issue, PR or file that has the detail instead of copying it.
