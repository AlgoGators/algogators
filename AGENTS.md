# Instructions for agents

Read [`docs/knowledge-base/`](docs/knowledge-base/README.md) before changing anything: it holds the
decisions already taken, how production runs, where things live, who owns what, and known traps.

Never optional:

- This repository is public. No secrets, hostnames, IP addresses, e-mail addresses or licensed data
  (IvyDB, Cboe) in files, commits, issues or pull requests.
- No AI attribution in commit messages or PR text: no AI `Co-authored-by`, no `Claude-Session:`,
  no `claude.ai/code` link, no "Generated with" footer.
- Squash merges; wait for every check before merging.
- Nothing touches production between 09:00 and 10:15 New York time.
- Read-only by default against production and its database; a write goes through a script a
  person reviews and runs.
- When you settle a question someone will ask again, add it to
  `docs/knowledge-base/decisions.md` in the same PR.
