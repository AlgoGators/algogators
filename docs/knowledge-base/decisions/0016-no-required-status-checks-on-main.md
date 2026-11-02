# 0016. The main ruleset gates reviews and CodeQL, not status checks

- **Status:** Accepted
- **Date:** 2026-08-19
- **Sources:** `gh api repos/AlgoGators/algogators/rules/branches/main`; #179

## Context

Gate jobs exist so a check could be required under a stable name, but none has been made required.

## Decision

The ruleset on `main` requires a PR with two approvals, code-owner review and CodeQL results, blocks deletion and force-push, and lets organisation admins bypass. It requires no status checks.

## Consequences

Waiting for every check before merging is a human rule (decision 0001); `gh pr merge --admin` does not wait.
