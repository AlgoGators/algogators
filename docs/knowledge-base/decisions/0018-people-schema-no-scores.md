# 0018. The people schema stores no scores; GitHub is the source of members

- **Status:** Accepted
- **Date:** 2026-08-22
- **Sources:** #7, #12; `platform/db/docs/people-schema-reference.md`

## Context

Grading is sensitive, and GitHub logins can be renamed.

## Decision

Grades stay in the grading workbook; the database keeps only `stage` and `outcome`. Members and teams mirror the GitHub organisation one way, keyed by the numeric `github_user_id`; the login is not stored.

## Consequences

Never add score columns. A member's identity survives a login rename.
