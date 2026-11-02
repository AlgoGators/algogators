# 0026. data-ngin deploys a CI-built image by digest

- **Status:** Accepted
- **Date:** 2026-10-08
- **Sources:** #16 (`9a9ab2ea`), #62 (`3fc45f00`); `services/data-ngin/deploy/README.md`

## Context

The box should never build anything, and the image that was tested should be the one that runs.

## Decision

A push to `prod` that touches data-ngin or platform/db code runs the quality gate, builds the Airflow image, parses the DAGs inside it, and rolls the box to that digest.

## Consequences

Airflow imports are only checked inside that image (decision 0028).
