# 0001. Merge and history rules

- **Status:** Accepted
- **Date:** 2026-10-29
- **Sources:** #179 (was AlgoGators/trade-ngin#269)

## Context

The same three rules kept being followed by hand and missed: squash messages copied every commit message (AI trailers included) onto `main`, and merges that moved stored numbers left production's stored history computed by an older engine.

## Decision

- Squash by default: one commit, the PR title with `(#n)`, the PR's author, no copied commit messages. A merge commit only when needed, and the PR says why.
- No AI attribution in commits or PR text: no AI `Co-authored-by`, no `Claude-Session:`, no `claude.ai/code` link, no "Generated with" footer. Human co-authors stay.
- A PR either says "moves no stored number" or lists what moved; when it moves stored numbers, the stored backtests and the live book's history are re-seeded after the merge, and the PR names who does it and when.

## Consequences

Reviewers check the three rules on every PR. The repository settings (squash only, blank squash message) and an attribution check are still to do in #179.
