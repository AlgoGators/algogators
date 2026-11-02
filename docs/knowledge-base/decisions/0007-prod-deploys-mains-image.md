# 0007. Prod deploys main's tested image; the C++ build is cached

- **Status:** Accepted
- **Date:** 2026-11-02
- **Sources:** #120, #121, #123, #125

## Context

A trade-ngin build and test took 49 minutes, and a prod deploy built and tested the same commit again before deploying.

## Decision

- A push to `main` builds and tests trade-ngin and pushes `ghcr.io/algogators/trade-ngin-engine:sha-<commit>` alongside the tests.
- A push to `prod` (a fast-forward of `main`) deploys that image once main's quality run for the commit passed, waiting for it if needed; with no image or a failed run it builds and tests the commit itself.
- The C++ build caches vcpkg's packages as files and compiler output with ccache, links with mold, uses `-g1` and precompiled headers for the tests, and builds no tests in the image.

## Consequences

A warm build and test takes about 3 minutes. Rejected: parallel `ctest -j` (tests share fixed working-directory files), ccache inside the Docker build (the GitHub cache does not keep BuildKit cache mounts), larger runners (a billing setting).
