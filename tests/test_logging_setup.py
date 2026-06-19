"""Behavior tests for console logging setup + colorization."""

import logging

import pytest

from inshust.config import load_config
from inshust.logging_setup import ColorFormatter, setup_logging


@pytest.fixture
def _isolate_root_logging():
    """Save/restore global logging state so setup_logging tests don't leak."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    names = ("instagrapi", "private_request", "public_request", "urllib3")
    saved_levels = {n: logging.getLogger(n).level for n in names}
    try:
        yield
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        for name, level in saved_levels.items():
            logging.getLogger(name).setLevel(level)


def _record(level: int, msg: str) -> logging.LogRecord:
    return logging.LogRecord("inshust.demo", level, "p", 1, msg, None, None)


def test_color_formatter_colorizes_level_and_message_when_enabled():
    out = ColorFormatter(use_color=True).format(_record(logging.WARNING, "watch out"))
    assert "\033[" in out  # ANSI escape present
    assert "WARNING" in out
    assert "watch out" in out


def test_color_formatter_is_plain_when_disabled():
    out = ColorFormatter(use_color=False).format(_record(logging.INFO, "plain message"))
    assert "\033[" not in out
    assert "INFO" in out
    assert "plain message" in out


def test_setup_logging_quiets_instagrapi_request_logs_at_info(_isolate_root_logging):
    setup_logging("INFO")
    assert logging.getLogger().level == logging.INFO
    assert logging.getLogger("instagrapi").level == logging.WARNING
    assert logging.getLogger("private_request").level == logging.WARNING
    assert logging.getLogger("public_request").level == logging.WARNING


def test_setup_logging_keeps_request_logs_at_debug(_isolate_root_logging):
    setup_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG
    assert logging.getLogger("instagrapi").level == logging.DEBUG


def test_setup_logging_is_idempotent_one_color_handler(_isolate_root_logging):
    setup_logging("INFO")
    setup_logging("INFO")
    color_handlers = [
        h for h in logging.getLogger().handlers if isinstance(h.formatter, ColorFormatter)
    ]
    assert len(color_handlers) == 1


def test_setup_logging_tolerates_unknown_level(_isolate_root_logging):
    setup_logging("NOPE")
    assert logging.getLogger().level == logging.INFO


def test_config_log_level_default_and_from_env():
    assert load_config(env={}).log_level == "INFO"
    assert load_config(env={"LOG_LEVEL": "DEBUG"}).log_level == "DEBUG"
