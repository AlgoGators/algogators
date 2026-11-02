# 0028. Airflow is not a workspace dependency

- **Status:** Accepted
- **Date:** 2026-10-08
- **Sources:** `services/data-ngin/pyproject.toml`; `svc-data-ngin.deploy.yml` (verify-dags); #16

## Context

Airflow's pins conflict with the rest of the uv workspace, and it cannot be installed in the Windows development environment.

## Decision

`apache-airflow` is installed only in `Dockerfile.airflow`. The deploy workflow's DAG parse inside the image is the only test of Airflow imports.

## Consequences

A DAG import error shows in the deploy, not in the quality workflow.
