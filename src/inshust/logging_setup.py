"""Console logging setup: clean, colored output + quieting instagrapi's noise.

instagrapi logs every private API call (full URLs) at INFO via the
``instagrapi`` / ``private_request`` / ``public_request`` loggers, which drowns
our own messages. :func:`setup_logging` installs a single colored stream handler
on the root logger, sets our level, and turns those request loggers down to
WARNING (unless we're at DEBUG). Colors are ANSI, applied only when writing to a
TTY and ``NO_COLOR`` is unset (``FORCE_COLOR`` forces them on).

It also provides interactive console handlers so a human-present run (the
``inshust-threads`` / ``inshust-backfill`` commands) can resolve an Instagram
login challenge or a 2FA prompt by typing the code, instead of pausing. The
always-on bot (``inshust``) deliberately does NOT wire these — it pauses and
alerts, because no human is at the terminal.
"""

from __future__ import annotations

import logging
import os
import sys

_LEVEL_COLORS = {
    logging.DEBUG: "\033[36m",  # cyan
    logging.INFO: "\033[32m",  # green
    logging.WARNING: "\033[33m",  # yellow
    logging.ERROR: "\033[31m",  # red
    logging.CRITICAL: "\033[1;37;41m",  # bold white on red
}
_DIM = "\033[2m"
_RESET = "\033[0m"

# instagrapi's per-request loggers spam full API URLs at INFO; quiet unless DEBUG.
_NOISY_LOGGERS = ("instagrapi", "private_request", "public_request", "urllib3")


class ColorFormatter(logging.Formatter):
    """Format as ``HH:MM:SS LEVEL name message``, colorized when ``use_color``."""

    def __init__(self, *, use_color: bool) -> None:
        super().__init__(datefmt="%H:%M:%S")
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, self.datefmt)
        msg = record.getMessage()
        if record.exc_info:
            msg = f"{msg}\n{self.formatException(record.exc_info)}"
        if self._use_color:
            color = _LEVEL_COLORS.get(record.levelno, "")
            return (
                f"{_DIM}{ts}{_RESET} {color}{record.levelname:<8}{_RESET} "
                f"{_DIM}{record.name}{_RESET} {msg}"
            )
        return f"{ts} {record.levelname:<8} {record.name} {msg}"


def _want_color(stream: object) -> bool:
    """Decide whether to emit ANSI color for ``stream``."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def _coerce_level(level: str | int) -> int:
    """Coerce a level name/number to a logging int, defaulting to INFO."""
    if isinstance(level, int):
        return level
    value = logging.getLevelName(str(level).upper())
    return value if isinstance(value, int) else logging.INFO


def setup_logging(level: str | int = "INFO") -> None:
    """Install one colored stream handler on root; quiet instagrapi's noise.

    Idempotent: existing root handlers are cleared first, so calling this once
    per entry point never stacks duplicate handlers. The ``instagrapi`` request
    loggers are pinned to WARNING unless ``level`` is DEBUG (then they pass
    through, for deep troubleshooting).
    """
    level_value = _coerce_level(level)
    root = logging.getLogger()
    root.setLevel(level_value)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(ColorFormatter(use_color=_want_color(sys.stderr)))
    root.addHandler(handler)
    noisy = level_value if level_value <= logging.DEBUG else logging.WARNING
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(noisy)


def console_challenge_code_handler(username: str, choice: object) -> str:
    """Prompt the operator for the login-challenge code Instagram just sent.

    Wired only into the human-present commands. instagrapi calls this with the
    delivery ``choice`` (EMAIL/SMS); returning the typed code lets instagrapi
    resolve the checkpoint instead of raising and pausing the bot.
    """
    target = getattr(choice, "name", None) or str(choice)
    return input(
        f"\n🔑 Instagram sent a verification code (via {target}) for {username}.\n"
        f"   Enter the code: "
    ).strip()


def console_two_factor_code_provider() -> str:
    """Prompt the operator for their 2FA code (when no TOTP seed is configured)."""
    return input("\n🔑 Enter your Instagram 2FA code: ").strip()
