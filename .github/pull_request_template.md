## What and why

<!-- One or two sentences. The "why" is the part reviewers cannot reconstruct. -->

## Which members does this touch?

<!--
Tick every one. If you ticked a member whose quality check did NOT run on this
PR, its `paths:` filter is wrong — fix the filter in the same PR, or CI is
lying about coverage for that service.
-->

- [ ] `libs/…`
- [ ] `services/…`
- [ ] `apps/…`
- [ ] `platform/contracts` — **fans out to every consuming service; confirm they all ran green**
- [ ] `platform/configs`
- [ ] CI / tooling only

## Checks

- [ ] Ran `just quality <path>` locally for each member touched
- [ ] Coverage floor unchanged, or lowered deliberately with a reason below
- [ ] No secrets, API keys, `.pem` files, or real position/PnL data in the diff
- [ ] Schema change is backward compatible, or every consumer is updated here

## Risk

<!--
For anything that touches order routing, position sizing, or live data ingest:
what is the blast radius if this is wrong, and how would you notice?
-->

## Before merging (#179)

<!--
The three standing rules (docs/knowledge-base/decisions/0001-merge-and-history-rules.md).
meta-attribution.yml fails the PR on the second; reviewers check the other two.
-->

- [ ] **Squash and merge.** A merge commit only when this PR needs one (for example an integration branch whose pull requests should stay separate on `main`), and this description says why.
- [ ] **No AI attribution** in the commits or this description: no AI `Co-authored-by`, no `Claude-Session:` line, no `claude.ai/code` link, no "Generated with" footer. Human co-authors stay.
- [ ] **Stored numbers:** <!-- write "moves no stored number", or list what moved (the stored 2y, 5y, 10y and 16y backtests, the live book's history), who re-seeds them after the merge, and when -->
