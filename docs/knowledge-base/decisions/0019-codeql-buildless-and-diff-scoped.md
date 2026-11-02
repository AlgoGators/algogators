# 0019. CodeQL is buildless and diff-scoped, with a weekly full scan

- **Status:** Accepted
- **Date:** 2026-08-24
- **Sources:** commits `060245d9`, `83d4df3a`, `5ef0bb36`, `d85660ed`; `.github/workflows/security.yml`

## Context

CodeQL's autobuild cannot provide the vcpkg dependency tree, and a docs-only change should not pay for a C++ extraction.

## Decision

CodeQL runs with `build-mode: none`, skips languages a push or PR did not touch, excludes `cpp/missing-return`, and scans everything on Mondays. The OpenSSF Scorecard job was dropped (it pointed at a nonexistent tag and graded posture nobody acted on).

## Consequences

A finding in an untouched language can wait until the Monday scan.
