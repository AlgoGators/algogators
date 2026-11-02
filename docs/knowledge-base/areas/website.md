# Website (algogators.com)

**LAST UPDATED:** 2026-11-02 · **Commit:** `8c534c1b` (main, the code this page was checked against)

The club's public site. Hand-written static HTML in `apps/website/site/`, no framework and no
bundler, served by GitHub Pages from this repository. The step-by-step deploy and rollback is the
runbook [`docs/runbooks/website-deploy.md`](../../runbooks/website-deploy.md); this page is the
background around it.

## What it is

| What | Where |
|---|---|
| Pages | `apps/website/site/*.html`: `index`, `who-we-are`, `what-we-do`, `how-we-train`, `research`, `apply` |
| Shared style and script | `site/assets/main.css`, `site/assets/main.js` |
| Headshots, research PDFs, application files | `site/Headshots/`, `site/ResearchFiles/`, `site/ApplicationFiles/` |
| Build and check scripts (Node, no dependencies) | `apps/website/scripts/` |
| Custom domain file | `site/CNAME` |
| Source before the import | `AlgoGators/AlgoGatorsWebsite` (not archived; its Pages site has no custom domain) |

It came in with the first import (#1, 2026-08-17) with its history; the commits from June 2026 are
the old repository's. Left out on purpose: the old self-updater (`Update.exe`, `update_tool.py`,
`_tool/`), unreferenced screenshots and session logs (`apps/website/README.md`).

## How it is served

From `gh api repos/AlgoGators/algogators/pages`:

- build type `workflow`: Pages serves whatever `actions/deploy-pages` last uploaded, not a branch;
- custom domain `algogators.com`; the certificate covers the apex and `www`; HTTPS is enforced;
- the domain is not verified for the organisation (`protected_domain_state: unverified`).

With a workflow build, GitHub takes the custom domain from the repository's Pages settings and
ignores a `CNAME` file; `site/CNAME` matches the setting but does not set it. Change the domain in
Settings, Pages.

The `github-pages` environment only accepts deploys from `main` and from tags `website/v*`.

This repository's Pages site is algogators.com, so it cannot host a second site. That is why the
issue tracker page is published from its own repository ([decision 0009](../decisions/0009-tracker-on-pages.md)).

## Workflows

| Workflow | Runs on | Does |
|---|---|---|
| `app-website.quality.yml` | PRs and pushes to `main` touching `apps/website/**`, the pnpm lock or workspace, or its workflows | `_quality-node.yml`: lint, typecheck, test, build, a 500 KB gzipped bundle budget. Gate check: `app-website quality` |
| `app-website.publish.yml` | push of a tag `website/v*`; Run workflow | quality again, then `_publish-static-site.yml` with `target: pages` |
| `_publish-static-site.yml` | called | `pnpm install --frozen-lockfile`, build only `apps/website`, upload `dist/`, deploy to Pages |

Run workflow on the publish workflow defaults to `dry-run: true`, which builds and skips the Pages
job. Untick it to deploy by hand.

## Changing content

1. Edit the HTML in `apps/website/site/`. A new image or PDF goes next to the others; the lint
   fails on any local `href` or `src` that does not resolve (`scripts/check-links.mjs`).
2. **People.** The roster on `who-we-are.html` is the one source of every headcount on the site.
   Add or remove the `<p class="nm">` tile (and the headshot). Leads appear twice by design, once
   under Leadership and once under their team; the count is of distinct names (`scripts/roster.mjs`).
3. **Application window.** Edit the three lines of `var APPLICATIONS` in `site/assets/main.js`
   (`term`, `closes`, `closesLabel`). The page flips its open and closed copy on `closes` in the
   browser, with no redeploy (#11).
4. Run `corepack pnpm --filter ./apps/website run sync`. It rewrites the counts, deadline and term
   checked into `site/` from those two sources. The tests fail with "run `pnpm sync`" if you skip it.
5. Run the checks: `just quality apps/website` (or `lint`, `typecheck`, `test` one by one).

Do not type a number or a date into a page by hand: mark it with `data-member-count`,
`data-section-count`, `data-deadline` or `data-term` and let `sync` and `build` fill it
(`scripts/stamp.mjs`). The tests reject a hardcoded headcount or application status.

## Preview

- Open `apps/website/site/index.html` in a browser. The checked-in numbers are a readable fallback
  for exactly this (`scripts/build.mjs`).
- To see what ships: `corepack pnpm --filter ./apps/website run build`, then open or serve
  `apps/website/dist/`. The build copies `site/` and stamps every page from the roster and the
  application config.

There is no preview deploy; the PR's quality check is the only CI view before a tag.

## Deploying

Merging does not deploy. Cut a tag `website/vX.Y.Z` on `main`; about a minute for the workflow and
up to ten for the CDN (`cache-control: max-age=600`). Check with the `last-modified` header. Steps
and rollback: the runbook. The version number is the tag's alone: `package.json` and the
release-please manifest still say 0.1.0, and nothing bumps them.

Tags so far: `website/v0.1.0` (2026-08-17) to `website/v0.3.0` (2026-08-30, PR #11), all from the
publish workflow.

## Decisions

- **2026-08-30 · Every number on the site has one source.** Headcounts come from the roster and the
  application dates from `APPLICATIONS`; `build` stamps `dist/`, `sync` stamps `site/`, tests lock
  it. Why: hand-typed counts had drifted (26 in four places, 34 on What We Do, 24 actually on the
  roster). **Source:** #11, commits `9896468d`, `950894ce`.
- **2026-08-30 · Deploys are tag-scoped, merges are not deploys.** Each member has its own tag
  prefix, so a website tag ships only `apps/website/dist`. **Source:** `4bd1a16b` (the runbook).
- **2026-08-24 · Research PDFs are hosted in the repository** (`site/ResearchFiles/`) instead of
  external links to the old Wix site, which had left three capstone links dead. **Source:**
  `9c24b448`.

## Gotchas

- **Merged is not live.** #79 and #80 (roster changes) merged on 2026-10-23 and no tag has been cut
  since `website/v0.3.0`; the live site's `last-modified` was still 2026-08-30 on 2026-11-01. Cut
  the next tag after a website merge. It already happened once with #2 (runbook).
- **`[hidden]` loses to any class that sets `display`.** The ticker once showed "open" and
  "closed" together. `main.css` forces `[hidden]` and a test keeps it (#11).
- **Use forward slashes in image paths.** A backslash `src` worked in browsers but pointed at a
  duplicate file; a test rejects it (#11).
- **Every headshot on disk must be referenced by a page** (a test). Delete the file when you remove
  a person.
- **`app-algolens-web.publish.yml` also deploys to this Pages site.** Do not run it with
  `dry-run` unticked; it would replace algogators.com (ci-and-deploys.md).
- **The shared pnpm lock is the one thing other members can break.** A bad `pnpm-lock.yaml` stops
  the publish before the deploy job; the site stays on its last version (runbook).
