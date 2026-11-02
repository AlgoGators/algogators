# Contributing

## Add A Member

1. Create the member directory under `libs/`, `services/`, or `apps/`.
2. Add one manifest so the repo can discover its language:
   - Python: `pyproject.toml`
   - Node: `package.json`
   - C++: `CMakeLists.txt`
3. For Python, add the path to `[tool.uv.workspace].members`.
4. For Node, add the path to `pnpm-workspace.yaml`.
5. Generate the workflow files:

   ```bash
   just new-service services services/new-service
   ```

6. Run the local checks:

   ```bash
   just check-paths
   ```

## Migration Rule

Move structure first, code second. A migration PR should be readable as either:

- scaffolding and CI ownership only, or
- actual source import and adaptation.

Do not mix both unless the codebase is small enough for the diff to stay clear.
