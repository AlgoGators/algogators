# Runbook: deploying algogators.com

`algogators.com` is GitHub Pages served from this repo (`AlgoGators/algogators`),
built from `apps/website`. The old `AlgoGators/AlgoGatorsWebsite` repo is not the
source of the live site — its Pages instance still exists at
`algogators.github.io/AlgoGatorsWebsite` but holds no custom domain.

## Merging to `main` does not deploy

`.github/workflows/app-website.publish.yml` triggers only on a `website/v*` tag
push. `app-website.quality.yml` *does* run on merges to `main`, so a merged PR
gets a green check and looks shipped while the live site is unchanged.

This has already bitten us once: PR #2 (`feature/website-bugfixes`) merged
2026-08-24 21:27 UTC, after the `website/v0.2.1` tag was cut at 19:51. Six
changed pages sat undeployed until `website/v0.2.2` on 2026-08-30.

**After merging a website PR, cut a tag.**

```bash
git checkout main && git pull --ff-only
git tag website/vX.Y.Z main
git push origin website/vX.Y.Z
```

Takes ~1 min for the workflow, plus up to 10 min for the Pages CDN
(`index.html` is served with `cache-control: max-age=600`).

Verify with the `last-modified` header rather than a browser, which may hold a
cached copy:

```bash
curl -sI https://algogators.com | grep -i last-modified
```

## Scope: a website tag deploys only the website

Every component has its own tag prefix and its own publish workflow:
`website/v*`, `algolens-web/v*`, `algoterminal/v*`, `algosystem/v*`,
`research-core/v*`, `trade-ngin/v*`, `data-ngin/v*`, `research-api/v*`. A
`website/v*` tag matches exactly one glob; the three services additionally gate
their rollout behind a separate `deploy` input.

The tag names a whole-repo commit, so the checkout includes in-progress work
from other teams, but the build is `pnpm --filter "./apps/website" run build`
and only `apps/website/dist` is uploaded to Pages. Unready code elsewhere on
`main` cannot reach algogators.com.

The one shared surface is `pnpm-lock.yaml` / `pnpm-workspace.yaml`. A broken
lockfile fails `pnpm install --frozen-lockfile` and the run stops before the
deploy job — the site stays on the previous version. There is no partial deploy.

## Rollback

Pages swaps the whole site atomically, so a bad deploy is fully replaced, not
merged. Two options:

1. **Fastest** — re-run the last good publish run. It re-checks-out that tag,
   rebuilds and redeploys.
   ```bash
   gh run rerun <run-id> -R AlgoGators/algogators
   ```
2. **Preferred for anything non-urgent** — cut a new tag at the old commit. The
   build job's bare `actions/checkout@v4` resolves to the pushed tag, so this
   rebuilds the old tree under a new version number.
   ```bash
   git tag website/vX.Y.Z <good-commit>
   git push origin website/vX.Y.Z
   ```

Do not delete and re-push an existing tag. It triggers a deploy, but the tag
history then misrepresents what shipped.
