# 0020. The website's numbers and dates have one source each

- **Status:** Accepted
- **Date:** 2026-08-30
- **Sources:** #11; commits `9896468d`, `950894ce`

## Context

Hand-typed counts had drifted across pages.

## Decision

Headcounts come from the who-we-are roster and the application window from `APPLICATIONS` in `main.js`; `build` stamps `dist/`, `pnpm sync` stamps `site/`, and tests reject hand-typed values.

## Consequences

Change the source, not the page.
