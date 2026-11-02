# apps/

Things a **human opens**: web front-ends, terminal UIs, desktop clients, CLIs
whose primary audience is a person rather than another program.

| | |
|---|---|
| Publishes to | static hosting, container registry, PyPI (console-scripts), release binaries |
| Release trigger | tag `<name>/vX.Y.Z` |
| Quality bar | moderate coverage, plus interaction tests (Playwright / Textual snapshots) |

The distinction from `services/` is who the consumer is, and it matters because
it changes what "tested" means. An app needs its *interface* exercised —
a rendered page, a keypress, a terminal at 80×24 — not just its functions.

## Planned occupants

| directory | source repo | archetype |
|---|---|---|
| `algolens-web/` | `AlgoLens` (`src/`, `public/`, `index.html`, `vite.config.ts`) | Vite + React 18 |
| `algoterminal/` | — | Textual TUI, not yet written |
