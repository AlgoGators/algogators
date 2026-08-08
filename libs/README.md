# libs/

Code that ships to a **package registry** and is consumed by other code.

A directory belongs here if the answer to "how does someone use this?" is
`pip install` / `npm install`, not "open a URL" or "it's already running."

| | |
|---|---|
| Publishes to | PyPI, npm, GHCR (as a base image) |
| Release trigger | tag `<name>/vX.Y.Z` |
| Quality bar | strictest in the repo — see below |

Libraries are held to the highest coverage floor and the widest interpreter /
runtime matrix, because a break here propagates to every consumer at once and
you cannot roll it back by redeploying. A service that breaks affects one
deployment; a library that breaks affects everyone who upgrades.

## Planned occupants

| directory | source repo | notes |
|---|---|---|
| `algosystem/` | `algosys` | published on PyPI as `algosystem`; Poetry today, uv workspace member after migration |
| `research-core/` | — | shared research calculations and data interfaces used by `research-api` and `algoterminal` |

Nothing has been migrated yet. See [CONTRIBUTING.md](../CONTRIBUTING.md) for the
add-a-service runbook.
