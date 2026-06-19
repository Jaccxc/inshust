"""The real ``instagrapi.Client``-backed adapter — the genuine I/O boundary.

This is the ONE module that imports instagrapi. It implements
:class:`~inshust.client.ClientPort` so the orchestration (listener/app) never
touches the network type directly and stays fully testable with the in-memory
``FakeClient``.

Responsibilities (card #3):

- **Auth/session.** Reuse a persisted session via ``load_settings`` when a
  session file exists (do NOT re-login when a valid session exists); otherwise
  ``login`` with credentials and ``dump_settings`` to persist. Supply a TOTP
  code from the configured seed when 2FA is required. On a challenge/checkpoint
  exception, STOP acting: mark a paused state, alert the operator (prominent
  log), back off once, and raise :class:`OperatorPausedError` — never loop.
- **Pacing.** Space API actions with a randomized human-like delay within the
  configured range, via an injectable sleeper (so tests assert it without
  sleeping).
- **Sending.** ``send_reply`` uses native quote-reply
  (``direct_send(reply_to_message=...)``) referencing the original message; if
  that cannot be constructed/accepted it FALLS BACK to a plain ``direct_send``
  that still delivers the text. (Retry/backoff is card #5 — a clean seam is
  left, not implemented here.)
- **Resolution.** ``resolve_media_pk_from_url`` delegates to
  ``client.media_pk_from_url``.
- **Polling.** ``poll_once`` fetches recent messages from the configured thread
  and feeds each (normalized to what ``extract_share`` expects) through
  ``Listener.handle_message``.

The instagrapi client is injectable (``client=`` for a ready mock, or
``client_factory=`` to build one), so every test runs offline with a mock.
"""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol

from inshust.config import Config

if TYPE_CHECKING:  # pragma: no cover - typing only
    from inshust.listener import Listener

logger = logging.getLogger(__name__)


class OperatorPausedError(Exception):
    """Raised when a challenge/checkpoint forces the bot to pause + alert.

    Signals that manual operator action is required. The adapter does NOT keep
    retrying after this — it backs off once and surfaces the paused state.
    """


class _InstagrapiClient(Protocol):
    """The slice of ``instagrapi.Client`` this adapter actually uses."""

    def load_settings(self, path: str) -> dict: ...

    def login(
        self,
        username: str | None = ...,
        password: str | None = ...,
        verification_code: str = ...,
    ) -> bool: ...

    def dump_settings(self, path: str) -> bool: ...

    def totp_generate_code(self, seed: str) -> str: ...

    def set_proxy(self, dsn: str | None) -> None: ...

    def set_locale(self, locale: str) -> None: ...

    def direct_send(self, text: str, thread_ids: list[int], reply_to_message: object = ...): ...

    def direct_messages(self, thread_id: int, amount: int = ...) -> list: ...

    def media_pk_from_url(self, url: str) -> str: ...


def _default_client_factory(config: Config) -> _InstagrapiClient:  # pragma: no cover - import seam
    """Build a real ``instagrapi.Client``. Never imported by tests (they inject)."""
    from instagrapi import Client

    delay_range = [config.action_delay_min_seconds, config.action_delay_max_seconds]
    return Client(delay_range=delay_range)


class InstagramAdapter:
    """A :class:`ClientPort` backed by ``instagrapi.Client``."""

    def __init__(
        self,
        config: Config,
        *,
        client: _InstagrapiClient | None = None,
        client_factory: Callable[[Config], _InstagrapiClient] = _default_client_factory,
        sleeper: Callable[[float], None] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._config = config
        self._client = client if client is not None else client_factory(config)
        self._sleeper = sleeper
        self._rng = rng or random.Random()
        self._paused = False

    @property
    def client(self) -> _InstagrapiClient:
        """The underlying instagrapi client (for advanced wiring)."""
        return self._client

    @property
    def paused(self) -> bool:
        """True once a challenge/checkpoint has forced a paused state."""
        return self._paused

    # --- auth / session --------------------------------------------------
    def login(self) -> None:
        """Authenticate, reusing a persisted session when one exists.

        Reuse path: if the session file exists, ``load_settings`` it and return
        without a fresh network login. Otherwise authenticate with credentials.
        """
        if Path(self._config.session_path).exists():
            self._client.load_settings(self._config.session_path)
            return
