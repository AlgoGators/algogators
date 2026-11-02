# docs/

One MkDocs Material site for the whole org, with a section per service.

Per-service reference docs are generated from source by `mkdocstrings` and live
next to the code (`services/<name>/docs/`); this directory holds what is *not*
service-specific: architecture, onboarding, runbooks, the trading-system
overview, and the nav that stitches everything together.

Built and published by `.github/workflows/security.yml`'s sibling docs job on
pushes to `main`.

- [`knowledge-base/`](knowledge-base/README.md): decisions, operations, where things live, people
  and gotchas, for people and agents new to the repository.
- [`runbooks/`](runbooks/): step-by-step procedures.
