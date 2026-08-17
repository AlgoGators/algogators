# website

The algogators.com marketing site, migrated from the `AlgoGatorsWebsite` repo.

Hand-authored static HTML in `site/`; no framework, no bundler. The npm
scripts exist so this member answers the same four CI gates as every other
node member:

- `lint` — every local `href`/`src` in the pages resolves to a real file
- `typecheck` — `node --check` syntax pass over the site JS and the scripts
- `test` — structural page checks (title, stylesheet, `lang` attribute)
- `build` — copies `site/` to `dist/` for the static-site publish workflow

`site/CNAME` carries the GitHub Pages custom domain.

Deliberately not migrated from the source repo: the self-updater tooling
(`Update.exe`, `update_tool.py`, `_tool/`), unreferenced design screenshots
(`shots/`, `Research/`), and Playwright session logs. Nothing in the pages
references any of it.
