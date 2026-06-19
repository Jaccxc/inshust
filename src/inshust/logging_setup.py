"""Console logging setup: clean, colored output + quieting instagrapi's noise.

instagrapi logs every private API call (full URLs) at INFO via the
``instagrapi`` / ``private_request`` / ``public_request`` loggers, which drowns
our own messages. :func:`setup_logging` installs a single colored stream handler
on the root logger and reformats everything (so instagrapi's request logs are
readable instead of raw ``INFO:private_request:...`` lines) while following the
chosen level — at INFO you SEE instagrapi's progress; only urllib3's
connection-pool noise is pinned to WARNING. Colors are ANSI, applied only when
writing to a TTY and ``NO_COLOR`` is unset (``FORCE_COLOR`` forces them on).

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
    # instagrapi's request loggers follow the chosen level (the formatter makes
    # them readable), so INFO shows real progress. urllib3's connection-pool
    # chatter is pure noise, so keep it at WARNING+ regardless of level.
    logging.getLogger("urllib3").setLevel(max(level_value, logging.WARNING))


def console_challenge_code_handler(username: str, choice: object) -> str:
    """Prompt the operator for the login-challenge code Instagram just sent.

    Wired only into the human-present commands. instagrapi calls this with the
    delivery ``choice`` (EMAIL/SMS); we tell the operator EXACTLY where the code
    was sent (a checkpoint code very often goes to email, not the phone), then
    return the typed code so instagrapi resolves the checkpoint instead of
    raising and pausing the bot.
    """
    target = str(getattr(choice, "name", None) or choice).upper()
    where = {
        "EMAIL": "your EMAIL inbox  (check spam/promotions too)",
        "SMS": "your phone via SMS",
    }.get(target, f"{target}")
    print(
        "\n"
        + "=" * 64
        + "\n  INSTAGRAM SECURITY CHALLENGE\n"
        + f"  A verification code was sent to {where}\n"
        + f"  for account @{username}.\n"
        + "=" * 64
    )
    return input("  Enter the verification code: ").strip()


def console_two_factor_code_provider() -> str:
    """Prompt the operator for their 2FA code (when no TOTP seed is configured)."""
    return input("\n🔑 Enter your Instagram 2FA code: ").strip()
