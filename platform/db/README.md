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

Lives under `platform/` rather than `libs/` because it is not a standalone
library: it is workspace-internal coupling that more than one service depends
on, and it is growing toward runtime access management. Consumed by
`services/research-api` and `services/data-ngin`. Deliberately NOT a
dependency of `libs/algosystem`: algosystem publishes to PyPI and must stay
installable outside the workspace, so it keeps its own copy of the config
reader.
