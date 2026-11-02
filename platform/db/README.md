# platform/db

Cross-cutting database access for AlgoGators services. Two layers:

* `DatabaseConfig` — the canonical DB settings object (DB_* env reading,
  validation, URL-escaped SQLAlchemy DSN, password-redacting repr) that every
  service used to reimplement.
* The **access gate** (`platform_db.gate`) — the seam where IAM decides who
  may open which database. A service describes itself and its target as an
  `AccessRequest` and asks an `AccessGate` for credentials:

  ```python
  from platform_db import AccessRequest, EnvAccessGate

  gate = EnvAccessGate()  # today: allow-all, credentials from DB_* env
  config = gate.authorize(AccessRequest(principal="data-ngin", database="markets"))
  engine = create_engine(config.url())
  ```

  `EnvAccessGate` reproduces the pre-gate behaviour (one shared credential, no
  policy). An IAM-backed gate implements the same one-method protocol against
  a central policy store and can hand back short-lived per-principal
  credentials; swapping it in changes no call sites. Denials raise
  `AccessDeniedError`, a `PermissionError` subclass.

* **Migrations** (`migrations/`) — plain SQL, applied by hand to the target
  database (pgAdmin or `psql`), never by CI or a deploy. `001_people_schema.sql`
  is the `people` schema: applicants, members, investors. Its design is in
  `docs/people-schema-reference.md`; `just migrate-idempotent platform/db` (from
  the repo root, needs Docker) proves every file applies twice cleanly and
  passes the assertion script under `tests/sql/`.

* **People import** (`platform_db.people_import`) — loads a Microsoft Forms
  `.xlsx` export (one recruiting cycle, one track) and the member roster CSV
  into the `people` schema, idempotently, one `import_batch` per run:

  ```sh
  # DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD in the environment (or --dsn)
  uv run python -m platform_db.people_import applications       --workbook "Fall 2026 ... Analyst Application.xlsx" --track analyst       --cycle fall-2026 --sheet Submissions --dry-run
  uv run python -m platform_db.people_import members --csv members.csv --dry-run
  uv run python -m platform_db.people_import close-cycle --cycle fall-2026 --dry-run
  ```

  `--dry-run` runs every write and rolls back, printing what would change.
  Applications create person / student / majors / attachments / application
  rows and the ranked team preferences; a "Composite Score" sheet's Interview
  Invite and Fund Invite columns set `stage` and `outcome`. The roster matches
  members to people by email, marks their pending application accepted, and
  records the team on `member_team`. `close-cycle`, run last, marks every
  application of the cycle still pending as rejected. The roster CSV columns are
  `first_name,last_name,email,team,is_leadership` with `team` a `people.team`
  slug or blank. Needs openpyxl and psycopg2, which the workspace dev group
  provides. Integration tests run when `PEOPLE_TEST_DSN` points at a database
  with the schema applied; see `tests/test_people_import_db.py`.

Lives under `platform/` rather than `libs/` because it is not a standalone
library: it is workspace-internal coupling that more than one service depends
on, and it is growing toward runtime access management. Consumed by
`services/research-api` and `services/data-ngin`. Deliberately NOT a
dependency of `libs/algosystem`: algosystem publishes to PyPI and must stay
installable outside the workspace, so it keeps its own copy of the config
reader.
