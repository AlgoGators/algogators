# 0005. Releasing the tradengin Python package

- **Status:** Accepted
- **Date:** 2026-11-02
- **Sources:** #191

## Context

`tradengin` (the engine's Python bindings) is on PyPI; 0.1.0 and 0.1.1 were released from trade-ngin with tags `vX.Y.Z`. In this repository a bare `v*` tag would clash with other members' releases.

## Decision

Release by pushing `tradengin/vX.Y.Z`, matching `services/trade-ngin/pyproject.toml`. `svc-trade-ngin.python.yml` builds Linux and Windows wheels for Python 3.10 to 3.13, tests each in a clean interpreter, and uploads from the `pypi` environment with the `PYPI_API_TOKEN` secret. A PR that touches the module or the engine API it binds builds and tests the Linux wheel for 3.12.

## Consequences

Bump the version before tagging (PyPI accepts a file name once). Trusted publishing would remove the stored token; the steps are in `services/trade-ngin/python/packaging/README.md`.
