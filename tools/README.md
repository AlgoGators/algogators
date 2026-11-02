# tools/

Developer scripts that are not part of any service and are not CI machinery
(CI machinery lives in `platform/ci/`).

Migration helpers for pulling the existing repos in — history-preserving
`git subtree` wrappers, Poetry→uv lockfile converters, import rewriters — will
land here as each service is migrated.
