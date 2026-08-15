# platform-db

Shared database plumbing for AlgoGators services: the canonical `DatabaseConfig`
(DB_* env reading, validation, URL-escaped SQLAlchemy DSN, password-redacting
repr) that every service used to reimplement.

Consumed by `services/research-api` and `services/data-ngin`. Deliberately NOT
a dependency of `libs/algosystem`: algosystem publishes to PyPI and must stay
installable outside the workspace, so it keeps its own copy (this lib is that
copy, promoted).
