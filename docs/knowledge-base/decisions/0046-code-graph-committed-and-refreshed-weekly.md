# 0046. The code graph is committed and refreshed weekly

- **Status:** Accepted
- **Date:** 2026-11-02
- **Sources:** this record's PR; `AGENTS.md` ("The code graph"); `.github/workflows/meta-graphify.yml`

## Context

Agents answer questions about the code faster and with less searching when they can query a graph
of it first. graphify builds one from the syntax tree, with no LLM, in about a minute. The graph of
this repository is about 52 MB (37,000 nodes; about 2.4 MB compressed, which is what git stores per
version). graphify's own setup rebuilds it in a git hook after every commit, which leaves a tracked
file modified in everyone's working tree and adds a version to the history at every refresh.

## Decision

- `graphify-out/graph.json` and `graphify-out/GRAPH_REPORT.md` are committed; graphify's other
  output (cache, manifest, analysis, the local root file) is ignored.
- `meta-graphify.yml` rebuilds them every Monday and on Run workflow, and pushes the result to
  `chore/graph-refresh`; a person opens the PR from the link in the run's summary. Workflows here
  cannot open PRs, and the setting that would allow it also lets the workflow token approve PRs.
- No per-commit git hook, and no agent-specific hook either: `AGENTS.md` gives every agent the same
  rules.
- The graph is code only (`--code-only`), SQL migrations included; documentation stays in
  `docs/knowledge-base/`. Communities keep numbered names (no LLM labelling).

## Consequences

The graph can be up to a week old: an agent confirms a detail in the code before relying on it. A
local `graphify update .` refreshes a personal copy; changes under `graphify-out/` are never
committed by hand. The history grows by about 2.4 MB a week. Every graphify version change is a
change to the pin in `AGENTS.md` and the workflow together.
