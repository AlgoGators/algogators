"""Runtime dependency composition for HTTP adapters."""

from werkzeug.security import check_password_hash, generate_password_hash

from research_api.infrastructure.db.postgres import execute_query, get_db_connection
from research_api.infrastructure.identity.dev_config import EnvironmentDevAuthConfig
from research_api.infrastructure.identity.repositories import PostgresUserRepository
from research_api.infrastructure.identity.security import WerkzeugPasswordHasher
from research_api.infrastructure.identity.sessions import FlaskJwtSessionIssuer
from research_api.infrastructure.portfolio.repositories import PostgresPortfolioRepository
from research_api.infrastructure.portfolio.strategy_registry import PostgresStrategyRegistry


def create_identity_dependencies(
    execute_query_func=None,
    connection_factory=None,
    verify_func=None,
    hash_func=None,
):
    users = PostgresUserRepository(
        execute_query_func=execute_query_func or execute_query,
        connection_factory=connection_factory or get_db_connection,
    )
    hasher = WerkzeugPasswordHasher(
        verify_func=verify_func or check_password_hash,
        hash_func=hash_func or generate_password_hash,
    )
    sessions = FlaskJwtSessionIssuer()
    return users, hasher, sessions


def create_portfolio_dependencies(connection_factory=None):
    registry = PostgresStrategyRegistry(connection_factory=connection_factory)
    reader = PostgresPortfolioRepository(connection_factory=connection_factory)
    return registry, reader


def create_dev_auth_config():
    return EnvironmentDevAuthConfig()
