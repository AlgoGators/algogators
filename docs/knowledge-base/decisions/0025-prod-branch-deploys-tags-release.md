# 0025. Production deploys from a shared prod branch; tags are releases

- **Status:** Accepted
- **Date:** 2026-10-08
- **Sources:** #16, #60, #61, #62, #120

## Context

A deploy is not a release, and a change to a shared workflow should not redeploy every service on the box.

## Decision

A fast-forward of `prod` deploys data-ngin, AlgoLens and trade-ngin, each only when its own code paths changed (workflow files left out). `<name>/v*` tags publish releases.

## Consequences

To redeploy after a workflow-only change, use the deploy workflow's dispatch. Note: `data-ngin/v*` and `research-api/v*` tag pushes still run a deploy job of their own (see the CI page).
