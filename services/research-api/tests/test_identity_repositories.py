"""Unit tests for PostgresUserRepository (infrastructure/identity/repositories.py)."""

from unittest.mock import MagicMock

import pytest
from research_api.infrastructure.db import postgres
from research_api.infrastructure.identity.repositories import PostgresUserRepository


def _row(**overrides):
    row = {
        "id": 7,
        "email": "member@example.com",
        "first_name": "Mem",
        "last_name": "Ber",
        "role": "general_member",
        "password_hash": "pbkdf2:something",
    }
    row.update(overrides)
    return row


class RecordingQuery:
    """Fake execute_query capturing the call and returning a canned row."""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, query, params=None, fetch_one=False):
        self.calls.append({"query": query, "params": params, "fetch_one": fetch_one})
        return self.result


def test_defaults_fall_back_to_postgres_module_functions():
    repo = PostgresUserRepository()
    assert repo.execute_query is postgres.execute_query
    assert repo.connection_factory is postgres.get_db_connection


def test_find_by_email_returns_user_and_binds_email():
    fake = RecordingQuery(_row())
    repo = PostgresUserRepository(execute_query_func=fake)

    user = repo.find_by_email("member@example.com")

    assert user is not None
    assert user.id == 7
    assert user.email == "member@example.com"
    assert user.role == "general_member"
    assert user.password_hash == "pbkdf2:something"
    call = fake.calls[0]
    assert call["params"] == ("member@example.com",)
    assert call["fetch_one"] is True
    assert "WHERE email = %s" in call["query"]


def test_find_by_email_returns_none_when_no_row():
    repo = PostgresUserRepository(execute_query_func=RecordingQuery(None))
    assert repo.find_by_email("nobody@example.com") is None


def test_find_by_id_returns_user_and_binds_id():
    fake = RecordingQuery(_row(id=42))
    repo = PostgresUserRepository(execute_query_func=fake)

    user = repo.find_by_id(42)

    assert user is not None
    assert user.id == 42
    call = fake.calls[0]
    assert call["params"] == (42,)
    assert call["fetch_one"] is True
    assert "WHERE id = %s" in call["query"]


def test_find_by_id_returns_none_when_no_row():
    repo = PostgresUserRepository(execute_query_func=RecordingQuery(None))
    assert repo.find_by_id(999) is None


def test_complete_registration_updates_commits_and_closes():
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = {
        "id": 7,
        "email": "member@example.com",
        "first_name": "New",
        "last_name": "Name",
        "role": "general_member",
    }
    repo = PostgresUserRepository(connection_factory=lambda: conn)

    user = repo.complete_registration("member@example.com", "hashed-pw", "New", "Name")

    assert user.id == 7
    assert user.first_name == "New"
    assert user.last_name == "Name"
    assert user.password_hash is None  # RETURNING clause omits the hash
    query, params = cursor.execute.call_args.args
    assert "UPDATE auth.users" in query
    assert params == ("hashed-pw", "New", "Name", "member@example.com")
    conn.commit.assert_called_once()
    conn.close.assert_called_once()


def test_complete_registration_closes_connection_on_error():
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.execute.side_effect = RuntimeError("db exploded")
    repo = PostgresUserRepository(connection_factory=lambda: conn)

    with pytest.raises(RuntimeError, match="db exploded"):
        repo.complete_registration("member@example.com", "hash", "A", "B")

    conn.close.assert_called_once()
    conn.commit.assert_not_called()
