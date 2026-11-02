# 0027. A failed pipeline task fails its DAG run, and failures file issues

- **Status:** Accepted
- **Date:** 2026-10-08
- **Sources:** #18 (`9715a11e`); `services/data-ngin/dags/`; #29

## Context

A trailing `all_done` staleness task marked runs green after the pipeline failed, and the box has no SMTP, so e-mail alerts went nowhere.

## Decision

The trailing task was dropped. Each DAG's failure callback files a `dag-failure` GitHub issue; staleness is left to the 13:00 UTC freshness check.

## Consequences

A red DAG run is a real failure; look for its `dag-failure` issue.
