"""Identity use-case tests driven by in-memory fakes (no DB, no Flask app)."""

from typing import cast

import pytest
from research_api.application.identity.ports import (
    DevAuthConfigPort,
    PasswordHasherPort,
    UserRepositoryPort,
)
from research_api.application.identity.use_cases import (
    AccountAlreadyRegisteredError,
    CheckEmail,
    DevLogin,
    DevLoginUnavailableError,
    EmailNotAuthorizedError,
    InvalidCredentialsError,
    Login,
    Logout,
    PasswordPolicyViolationError,
    RegisterUser,
    UserNotFoundError,
    VerifySession,
)
from research_api.domain.identity.models import User


class FakeUsers:
    """UserRepositoryPort backed by dicts; records registration calls."""

    def __init__(self, users: list[User] | None = None):
        self.by_email = {u.email: u for u in (users or [])}
        self.by_id = {str(u.id): u for u in (users or [])}
        self.registration_calls: list[tuple[str, str, str, str]] = []

    def find_by_email(self, email: str) -> User | None:
        return self.by_email.get(email)

    def find_by_id(self, user_id: str) -> User | None:
        return self.by_id.get(user_id)

    def complete_registration(
        self, email: str, password_hash: str, first_name: str, last_name: str
    ) -> User:
        self.registration_calls.append((email, password_hash, first_name, last_name))
        existing = self.by_email[email]
        return User(
            id=existing.id,
            email=email,
            first_name=first_name,
            last_name=last_name,
            role=existing.role,
            password_hash=password_hash,
        )


class FakeHasher:
    """PasswordHasherPort with a transparent, deterministic scheme."""

    def hash(self, password: str) -> str:
        return f"hashed:{password}"

    def verify(self, password_hash: str, password: str) -> bool:
        return password_hash == f"hashed:{password}"


class FakeDevConfig:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def is_enabled(self) -> bool:
        return self.enabled

    def user_id(self) -> int:
        return 42

    def user_email(self) -> str:
        return "dev@example.com"

    def user_role(self) -> str:
        return "admin"


def users_port(repo: FakeUsers) -> UserRepositoryPort:
    return cast(UserRepositoryPort, repo)


def hasher_port() -> PasswordHasherPort:
    return cast(PasswordHasherPort, FakeHasher())


REGISTERED = User(
    id=1,
    email="alice@example.com",
    first_name="Alice",
    last_name="Adams",
    role="general_member",
    password_hash="hashed:correct horse battery",
)
PREAUTHORIZED = User(id=2, email="bob@example.com", password_hash=None)


# --- Login -------------------------------------------------------------------


def test_login_returns_user_on_valid_credentials():
    repo = FakeUsers([REGISTERED])

    user = Login(users_port(repo), hasher_port()).execute(
        "alice@example.com", "correct horse battery"
    )

    assert user is REGISTERED


def test_login_rejects_unknown_email():
    repo = FakeUsers([])

    with pytest.raises(InvalidCredentialsError):
        Login(users_port(repo), hasher_port()).execute("nobody@example.com", "whatever")


def test_login_rejects_preauthorized_user_without_password():
    repo = FakeUsers([PREAUTHORIZED])

    with pytest.raises(InvalidCredentialsError):
        Login(users_port(repo), hasher_port()).execute("bob@example.com", "anything at all")


def test_login_rejects_wrong_password():
    repo = FakeUsers([REGISTERED])

    with pytest.raises(InvalidCredentialsError):
        Login(users_port(repo), hasher_port()).execute("alice@example.com", "wrong password")


# --- VerifySession -----------------------------------------------------------


def test_verify_session_returns_user():
    repo = FakeUsers([REGISTERED])

    assert VerifySession(users_port(repo)).execute("1") is REGISTERED


def test_verify_session_raises_when_user_row_is_gone():
    repo = FakeUsers([])

    with pytest.raises(UserNotFoundError):
        VerifySession(users_port(repo)).execute("999")


# --- CheckEmail --------------------------------------------------------------


def test_check_email_unknown_address_is_404():
    result = CheckEmail(users_port(FakeUsers([]))).execute("nobody@example.com")

    assert result.exists is False
    assert result.registered is False
    assert result.status_code == 404
    assert "not found" in result.message.lower()


def test_check_email_registered_account():
    result = CheckEmail(users_port(FakeUsers([REGISTERED]))).execute("alice@example.com")

    assert result.exists is True
    assert result.registered is True
    assert result.status_code == 200
    assert "login" in result.message.lower()


def test_check_email_preauthorized_but_unregistered():
    result = CheckEmail(users_port(FakeUsers([PREAUTHORIZED]))).execute("bob@example.com")

    assert result.exists is True
    assert result.registered is False
    assert result.status_code == 200
    assert "registration" in result.message.lower()


def test_check_email_treats_empty_string_hash_as_unregistered():
    repo = FakeUsers([User(id=3, email="carol@example.com", password_hash="")])

    result = CheckEmail(users_port(repo)).execute("carol@example.com")

    assert result.registered is False


# --- RegisterUser ------------------------------------------------------------


def test_register_rejects_short_password_before_touching_the_repo():
    repo = FakeUsers([PREAUTHORIZED])

    with pytest.raises(PasswordPolicyViolationError, match="at least 12 characters"):
        RegisterUser(users_port(repo), hasher_port()).execute(
            "bob@example.com", "short", "Bob", "Brown"
        )

    assert repo.registration_calls == []


def test_register_rejects_email_not_in_preauthorized_table():
    repo = FakeUsers([])

    with pytest.raises(EmailNotAuthorizedError):
        RegisterUser(users_port(repo), hasher_port()).execute(
            "stranger@example.com", "a perfectly long password", "S", "T"
        )

    assert repo.registration_calls == []


def test_register_rejects_already_registered_account():
    repo = FakeUsers([REGISTERED])

    with pytest.raises(AccountAlreadyRegisteredError):
        RegisterUser(users_port(repo), hasher_port()).execute(
            "alice@example.com", "a perfectly long password", "Alice", "Adams"
        )

    assert repo.registration_calls == []


def test_register_happy_path_stores_the_hash_not_the_password():
    repo = FakeUsers([PREAUTHORIZED])

    user = RegisterUser(users_port(repo), hasher_port()).execute(
        "bob@example.com", "a perfectly long password", "Bob", "Brown"
    )

    assert repo.registration_calls == [
        ("bob@example.com", "hashed:a perfectly long password", "Bob", "Brown")
    ]
    assert user.email == "bob@example.com"
    assert user.first_name == "Bob"
    assert user.last_name == "Brown"
    assert user.password_hash == "hashed:a perfectly long password"


# --- DevLogin / Logout -------------------------------------------------------


def test_dev_login_unavailable_when_disabled():
    with pytest.raises(DevLoginUnavailableError):
        DevLogin(cast(DevAuthConfigPort, FakeDevConfig(enabled=False))).execute()


def test_dev_login_builds_user_from_config():
    user = DevLogin(cast(DevAuthConfigPort, FakeDevConfig(enabled=True))).execute()

    assert user.id == 42
    assert user.email == "dev@example.com"
    assert user.first_name == "Dev"
    assert user.last_name == "User"
    assert user.role == "admin"
    assert user.password_hash is None


def test_logout_is_a_no_op():
    # Declared `-> None`; the contract is simply that it does not raise.
    Logout().execute()
