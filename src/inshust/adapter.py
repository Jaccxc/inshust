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
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from inshust.config import Config

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


def _to_epoch(timestamp: object) -> int:
    """Coerce an instagrapi ``timestamp`` to epoch seconds (UTC int).

    instagrapi exposes ``DirectMessage.timestamp`` as a ``datetime``; tolerate
    an already-numeric epoch too so the seam is robust to fakes/raw payloads.
    """
    if hasattr(timestamp, "timestamp"):
        return int(timestamp.timestamp())  # datetime -> epoch seconds
    try:
        return int(timestamp)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


class NormalizedMessage:
    """A duck-typed message carrying exactly what the pipeline reads.

    The listener + ``extract_share`` read ``id``/``item_type``/``clip``/
    ``media_share``/the xma fields plus ``user_id``/``username``/``timestamp``
    (epoch int)/``client_context``. This adapts an instagrapi ``DirectMessage``
    (whose ``timestamp`` is a ``datetime`` and which carries no username) to
    that shape, so the orchestration never sees an instagrapi type directly.
    """

    __slots__ = (
        "id",
        "user_id",
        "username",
        "item_type",
        "timestamp",
        "client_context",
        "clip",
        "media_share",
        "xma_share",
        "generic_xma",
    )

    def __init__(
        self,
        *,
        id: str,
        user_id: str,
        username: str,
        item_type: str | None,
        timestamp: int,
        client_context: str | None,
        clip: object,
        media_share: object,
        xma_share: object,
        generic_xma: object,
    ) -> None:
        self.id = id
        self.user_id = user_id
        self.username = username
        self.item_type = item_type
        self.timestamp = timestamp
        self.client_context = client_context
        self.clip = clip
        self.media_share = media_share
        self.xma_share = xma_share
        self.generic_xma = generic_xma


def normalize_direct_message(
    dm: object, users_by_id: dict[str, str] | None = None
) -> NormalizedMessage:
    """Adapt an instagrapi ``DirectMessage`` to a :class:`NormalizedMessage`.

    ``users_by_id`` maps a sender ``user_id`` to a username (built from the
    thread's user list). An unknown sender falls back to the user id so the
    pipeline never crashes on a missing name.
    """
    users_by_id = users_by_id or {}
    user_id = str(getattr(dm, "user_id", "") or "")
    username = users_by_id.get(user_id, user_id)
    return NormalizedMessage(
        id=str(getattr(dm, "id", "")),
        user_id=user_id,
        username=username,
        item_type=getattr(dm, "item_type", None),
        timestamp=_to_epoch(getattr(dm, "timestamp", 0)),
        client_context=getattr(dm, "client_context", None),
        clip=getattr(dm, "clip", None),
        media_share=getattr(dm, "media_share", None),
        xma_share=getattr(dm, "xma_share", None),
        generic_xma=getattr(dm, "generic_xma", None),
    )


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
        from instagrapi.exceptions import ChallengeError

        self._apply_client_settings()
        try:
            if Path(self._config.session_path).exists():
                self._client.load_settings(self._config.session_path)
                return

            self._fresh_login()
            self._client.dump_settings(self._config.session_path)
        except ChallengeError as exc:
            self._pause_on_challenge(exc)

    def _apply_client_settings(self) -> None:
        """Apply optional proxy + locale from config for detection consistency.

        The proxy is only set when configured (avoid disturbing a default
        direct connection). The locale always has a sensible default, so it is
        applied so the device/headers stay consistent across runs.
        """
        if self._config.proxy:
            self._client.set_proxy(self._config.proxy)
        self._client.set_locale(self._config.locale)

    def _fresh_login(self) -> None:
        """Authenticate with credentials (no persisted session available).

        Handles 2FA: when Instagram demands a verification code, generate a
        TOTP from the configured seed and retry the login with that code.
        """
        from instagrapi.exceptions import TwoFactorRequired

        try:
            self._client.login(self._config.username, self._config.password)
        except TwoFactorRequired:
            code = self._client.totp_generate_code(self._config.totp_seed)
            self._client.login(
                self._config.username,
                self._config.password,
                verification_code=code,
            )

    def _pause_on_challenge(self, exc: Exception) -> None:
        """Enter the paused state on a challenge/checkpoint: alert + back off.

        Logs a prominent (CRITICAL) operator alert, marks the bot paused, backs
        off ONCE, then raises :class:`OperatorPausedError`. Deliberately does
        NOT retry/loop — an Instagram checkpoint needs a human, and hammering it
        raises the ban risk this whole design is built to avoid.
        """
        self._paused = True
        logger.critical(
            "INSTAGRAM CHALLENGE/CHECKPOINT — bot PAUSED, operator action required: %s",
            exc,
        )
        self._sleep(self._config.challenge_backoff_seconds)
        raise OperatorPausedError(str(exc)) from exc

    # --- pacing ----------------------------------------------------------
    def _sleep(self, seconds: float) -> None:
        """Sleep via the injectable sleeper (real ``time.sleep`` by default)."""
        if self._sleeper is not None:
            self._sleeper(seconds)
        else:  # pragma: no cover - exercised only with the real default
            import time

            time.sleep(seconds)

    def _delay(self) -> None:
        """Pause a randomized human-like interval within the configured range."""
        low = self._config.action_delay_min_seconds
        high = self._config.action_delay_max_seconds
        self._sleep(self._rng.uniform(low, high))

    def _ensure_active(self) -> None:
        """Refuse to act while paused (a challenge needs a human first)."""
        if self._paused:
            raise OperatorPausedError("bot is paused awaiting operator action")

    # --- ClientPort: sending ---------------------------------------------
    def send_reply(
        self,
        thread_id: str,
        text: str,
        original_message_id: str,
        original_client_context: str | None,
    ) -> None:
        """Send ``text`` into ``thread_id``, quoting the original share.

        Prefers a native quote-reply via ``direct_send(reply_to_message=...)``
        referencing the original message (reconstructed from its id +
        client_context). If a native quote-reply cannot be constructed or is
        rejected by the server, FALLS BACK to a plain ``direct_send`` that still
        delivers the text into the thread.

        A paused bot refuses to send. Each send is preceded by a randomized
        human-like delay. Retry/backoff on transient send errors is card #5 —
        the fallback here is the quote-reply-vs-plain seam, not retry; a single
        clean ``_send`` choke point is left for #5 to wrap.
        """
        self._ensure_active()
        thread_ids = [int(thread_id)]
        self._delay()

        reply_to = self._build_reply_to(original_message_id, original_client_context)
        if reply_to is not None:
            try:
                self._client.direct_send(text, thread_ids=thread_ids, reply_to_message=reply_to)
                return
            except Exception:
                logger.warning(
                    "native quote-reply failed for message %s; falling back to plain send",
                    original_message_id,
                    exc_info=True,
                )

        # Fallback: a plain send that still delivers the text.
        self._client.direct_send(text, thread_ids=thread_ids)

    @staticmethod
    def _build_reply_to(original_message_id: str, original_client_context: str | None):
        """Reconstruct the minimal ``DirectMessage`` the server quotes against.

        ``direct_send`` reads ``.id`` and ``.client_context`` off the message to
        build the ``replied_to_item_id``/``replied_to_client_context`` payload.
        Returns ``None`` if even a minimal message cannot be built, so the
        caller falls back to a plain send.
        """
        from datetime import UTC, datetime

        try:
            from instagrapi.types import DirectMessage

            return DirectMessage(
                id=original_message_id,
                timestamp=datetime.now(UTC),
                client_context=original_client_context,
            )
        except Exception:  # pragma: no cover - defensive: bad/missing id
            logger.warning(
                "could not construct reply_to_message for %s", original_message_id, exc_info=True
            )
            return None

    # --- ClientPort: resolution ------------------------------------------
    def resolve_media_pk_from_url(self, url: str) -> str:
        """Resolve a share ``url`` to its canonical media ``pk``.

        Delegates to ``client.media_pk_from_url`` and normalizes the result to
        ``str`` for the canonical :class:`~inshust.models.MediaKey`. Spaced by a
        human-like delay; refuses while paused.
        """
        self._ensure_active()
        self._delay()
        return str(self._client.media_pk_from_url(url))
