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
