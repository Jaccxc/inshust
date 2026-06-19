"""Typed configuration loaded from environment / ``.env``.

No business logic. ``load_config`` reads from a supplied ``env`` mapping
(tests pass one explicitly) or, by default, from the process environment after
loading a ``.env`` file via python-dotenv.

The dedupe pipeline uses ``thread_id``, ``db_path`` and ``timezone``. The
auth/session/proxy/locale/pacing/poll fields drive the instagrapi adapter
(card #3). The realtime/backfill seams (cards #4-#6) extend this without
rewrites.
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
DEFAULT_ACTION_DELAY_MIN_SECONDS = 2.0
DEFAULT_ACTION_DELAY_MAX_SECONDS = 6.0
DEFAULT_CHALLENGE_BACKOFF_SECONDS = 300.0
DEFAULT_LOCALE = "en_US"
DEFAULT_POLL_AMOUNT = 20
DEFAULT_SEND_MAX_RETRIES = 3
DEFAULT_SEND_BACKOFF_BASE_SECONDS = 2.0
DEFAULT_RECONNECT_MAX_BACKOFF_SECONDS = 300.0


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
    locale: str = DEFAULT_LOCALE
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS
    poll_amount: int = DEFAULT_POLL_AMOUNT
    action_delay_min_seconds: float = DEFAULT_ACTION_DELAY_MIN_SECONDS
    action_delay_max_seconds: float = DEFAULT_ACTION_DELAY_MAX_SECONDS
    challenge_backoff_seconds: float = DEFAULT_CHALLENGE_BACKOFF_SECONDS

    # --- resilience (card #6) --------------------------------------------
    # Retry count + exponential backoff base for transient group-send 500s,
    # and the cap on the realtime reconnect backoff so it never grows unbounded.
    send_max_retries: int = DEFAULT_SEND_MAX_RETRIES
    send_backoff_base_seconds: float = DEFAULT_SEND_BACKOFF_BASE_SECONDS
    reconnect_max_backoff_seconds: float = DEFAULT_RECONNECT_MAX_BACKOFF_SECONDS


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
        locale=env.get("IG_LOCALE") or DEFAULT_LOCALE,
        poll_interval_seconds=int(
            env.get("POLL_INTERVAL_SECONDS") or DEFAULT_POLL_INTERVAL_SECONDS
        ),
        poll_amount=int(env.get("POLL_AMOUNT") or DEFAULT_POLL_AMOUNT),
        action_delay_min_seconds=float(
            env.get("ACTION_DELAY_MIN") or DEFAULT_ACTION_DELAY_MIN_SECONDS
        ),
        action_delay_max_seconds=float(
            env.get("ACTION_DELAY_MAX") or DEFAULT_ACTION_DELAY_MAX_SECONDS
        ),
        challenge_backoff_seconds=float(
            env.get("CHALLENGE_BACKOFF_SECONDS") or DEFAULT_CHALLENGE_BACKOFF_SECONDS
        ),
        send_max_retries=int(env.get("SEND_MAX_RETRIES") or DEFAULT_SEND_MAX_RETRIES),
        send_backoff_base_seconds=float(
            env.get("SEND_BACKOFF_BASE_SECONDS") or DEFAULT_SEND_BACKOFF_BASE_SECONDS
        ),
        reconnect_max_backoff_seconds=float(
            env.get("RECONNECT_MAX_BACKOFF_SECONDS") or DEFAULT_RECONNECT_MAX_BACKOFF_SECONDS
        ),
    )
