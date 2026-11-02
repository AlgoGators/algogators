# 0009. The issue tracker page is published from its own repository

- **Status:** Accepted
- **Date:** 2026-11-02
- **Sources:** #89, #90, #192

## Context

The tracker page could only be rebuilt from one machine, and this repository's Pages site already serves algogators.com.

## Decision

`tools/issue-tracker/sync.py` builds the page. The public repository `AlgoGators/issue-tracker` runs it hourly (and on Run workflow) and publishes https://algogators.github.io/issue-tracker/. The order of work is the list in #156.

## Consequences

Every repository the script reads is public, so the publishing repository stores no secret. A change to the script shows on the page at the next hourly build.
