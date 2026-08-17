"""Tests for shared logging configuration helpers."""

import logging

import pytest
from algosystem.shared.logging import get_logger, setup_logging


@pytest.fixture
def root_logger():
    """Snapshot the root logger and restore it after the test.

    setup_logging mutates the process-wide root logger (removes handlers,
    changes level), which would otherwise leak into other tests. Handlers
    created during the test are closed so temp log files can be deleted
    on Windows.
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    yield root
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        if handler not in saved_handlers:
            handler.close()
    for handler in saved_handlers:
        root.addHandler(handler)
    root.setLevel(saved_level)


class TestSetupLogging:
    def test_defaults_to_info_on_root_logger(self, root_logger):
        logger = setup_logging()
        assert logger is root_logger
        assert logger.level == logging.INFO

    def test_explicit_level_is_applied(self, root_logger):
        logger = setup_logging(level="DEBUG")
        assert logger.level == logging.DEBUG
        assert all(h.level == logging.DEBUG for h in logger.handlers)

    def test_level_is_case_insensitive(self, root_logger):
        logger = setup_logging(level="warning")
        assert logger.level == logging.WARNING

    def test_unknown_level_falls_back_to_info(self, root_logger):
        logger = setup_logging(level="NOT_A_LEVEL")
        assert logger.level == logging.INFO

    def test_installs_single_console_handler(self, root_logger):
        logger = setup_logging()
        assert len(logger.handlers) == 1
        handler = logger.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        fmt = handler.formatter._fmt
        assert fmt == "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    def test_removes_preexisting_handlers(self, root_logger):
        sentinel = logging.NullHandler()
        root_logger.addHandler(sentinel)
        logger = setup_logging()
        assert sentinel not in logger.handlers

    def test_repeated_setup_does_not_accumulate_handlers(self, root_logger):
        setup_logging()
        setup_logging()
        logger = setup_logging()
        assert len(logger.handlers) == 1

    def test_log_file_adds_file_handler_and_creates_directory(self, root_logger, tmp_path):
        log_file = tmp_path / "logs" / "nested" / "app.log"
        logger = setup_logging(level="INFO", log_file=str(log_file))

        file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 1
        assert log_file.parent.is_dir()

        logger.info("hello from the test")
        file_handlers[0].flush()
        content = log_file.read_text()
        assert "hello from the test" in content
        assert "- root - INFO -" in content

    def test_file_handler_respects_level(self, root_logger, tmp_path):
        log_file = tmp_path / "app.log"
        logger = setup_logging(level="ERROR", log_file=str(log_file))

        logger.info("should be filtered")
        logger.error("should be written")
        for handler in logger.handlers:
            handler.flush()

        content = log_file.read_text()
        assert "should be filtered" not in content
        assert "should be written" in content


class TestGetLogger:
    def test_named_logger_is_returned(self, root_logger):
        setup_logging()
        logger = get_logger("algosystem.test.child")
        assert logger.name == "algosystem.test.child"
        assert logger is logging.getLogger("algosystem.test.child")

    def test_without_name_returns_root(self, root_logger):
        setup_logging()
        assert get_logger() is root_logger

    def test_configures_root_when_unconfigured(self, root_logger):
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

        get_logger("anything")
        assert len(root_logger.handlers) == 1
        assert isinstance(root_logger.handlers[0], logging.StreamHandler)

    def test_does_not_reconfigure_when_handlers_exist(self, root_logger):
        setup_logging(level="DEBUG")
        existing = root_logger.handlers[:]
        get_logger("anything")
        assert root_logger.handlers == existing
