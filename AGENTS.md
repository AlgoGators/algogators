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
  `docs/knowledge-base/decisions/` (one file per decision) in the same PR.

## The code graph (graphify)

`graphify-out/graph.json` is a graph of the repository's code (files, classes, functions, imports
and calls; built from the syntax tree, no LLM), and `graphify-out/GRAPH_REPORT.md` summarises it.
`.github/workflows/meta-graphify.yml` rebuilds both every Monday; the report's first lines name the
commit it was built from.

Set up once: `uv tool install "graphifyy[sql]==0.9.58"` (or `pipx install "graphifyy[sql]==0.9.58"`).

- For a question about the code, start with the graph: `graphify query "<question>"`,
  `graphify explain "<symbol>"`, `graphify path "<A>" "<B>"`, and `graphify affected "<symbol>"`
  for what a change touches. Read `GRAPH_REPORT.md` only for a broad architecture view.
- The graph can be a week old. Confirm a detail in the code before relying on it. For a fresh local
  copy run `graphify update .`, but **do not commit changes under `graphify-out/`**: the weekly
  refresh does that.
- The graph holds only code symbols and repository paths. Documentation is in
  `docs/knowledge-base/`, not in the graph.
