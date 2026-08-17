"""Pytest fixtures for the AlgoLens backend.

The app is a module-level singleton created at import time, and its fail-closed
behavior (JWT/CORS) is evaluated then. So we configure a safe DEVELOPMENT env
BEFORE importing app, and exercise the production fail-closed paths in separate
subprocesses (see test_security.py) where the import is expected to raise.
"""

import contextlib
import os

import pytest

# Safe dev config so `import research_api.app` succeeds and does not require a real DB/secret.
os.environ.setdefault("FLASK_ENV", "development")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-pytest")


@pytest.fixture
def client():
    import research_api.app as app_module

    app_module.app.config.update(TESTING=True)
    # Clear rate-limit counters so tests don't bleed into each other.
    with contextlib.suppress(Exception):
        app_module.limiter.reset()
    with app_module.app.test_client() as c:
        yield c
