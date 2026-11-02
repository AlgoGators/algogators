"""Tests for the shared error taxonomy."""

import pytest
import research_core
from research_core.errors import ApplicationError, NotFoundError, ValidationError


def test_not_found_is_an_application_error() -> None:
    assert issubclass(NotFoundError, ApplicationError)


def test_validation_is_an_application_error() -> None:
    assert issubclass(ValidationError, ApplicationError)


def test_base_catches_subclasses() -> None:
    with pytest.raises(ApplicationError):
        raise NotFoundError("strategy 42")
    with pytest.raises(ApplicationError):
        raise ValidationError("bad payload")


def test_message_is_preserved() -> None:
    assert str(NotFoundError("strategy 42")) == "strategy 42"


def test_package_reexports_public_api() -> None:
    assert research_core.ApplicationError is ApplicationError
    assert research_core.NotFoundError is NotFoundError
    assert research_core.ValidationError is ValidationError
