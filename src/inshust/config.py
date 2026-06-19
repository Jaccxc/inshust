"""Typed configuration loaded from environment / ``.env``.

No business logic. ``load_config`` reads from a supplied ``env`` mapping
(tests pass one explicitly) or, by default, from the process environment after
loading a ``.env`` file via python-dotenv.

This slice only *uses* ``thread_id``, ``db_path`` and ``timezone``. The
credential/session/proxy/poll fields are populated as a forward seam for cards
#3-#6 but are not consumed yet.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_DB_PATH = "./data/inshust.db"
DEFAULT_TIMEZONE = "Asia/Taipei"
DEFAULT_SESSION_PATH = "./data/session.json"
DEFAULT_POLL_INTERVAL_SECONDS = 45


@dataclass(frozen=True)
class Config:
    """Runtime configuration for the bot.

    Required for this slice: ``thread_id``, ``db_path``, ``timezone``.
    Everything else is a seam for later cards.
    """

    thread_id: str
    db_path: str = DEFAULT_DB_PATH
    timezone: str = DEFAULT_TIMEZONE

    # --- seams for later cards (present, unused in this slice) -----------
    username: str | None = None
    password: str | None = None
    totp_seed: str | None = None
    session_path: str = DEFAULT_SESSION_PATH
    proxy: str | None = None
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Build a :class:`Config` from ``env`` (defaults to the process env).

    When ``env`` is ``None`` a ``.env`` file is loaded (if present) and the
    process environment is used. Passing an explicit mapping keeps tests
    hermetic — no .env, no real environment.
    """
    if env is None:
        load_dotenv()
        env = os.environ

    def _opt(key: str) -> str | None:
        value = env.get(key)
        return value if value else None

    return Config(
        thread_id=env.get("IG_THREAD_ID", ""),
        db_path=env.get("DB_PATH") or DEFAULT_DB_PATH,
        timezone=env.get("TZ") or DEFAULT_TIMEZONE,
        username=_opt("IG_USERNAME"),
        password=_opt("IG_PASSWORD"),
        totp_seed=_opt("IG_TOTP_SEED"),
        session_path=env.get("IG_SESSION_PATH") or DEFAULT_SESSION_PATH,
        proxy=_opt("IG_PROXY"),
        poll_interval_seconds=int(
            env.get("POLL_INTERVAL_SECONDS") or DEFAULT_POLL_INTERVAL_SECONDS
        ),
    )
