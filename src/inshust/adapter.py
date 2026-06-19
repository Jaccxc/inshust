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
from typing import TYPE_CHECKING, Protocol

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

    def direct_thread(self, thread_id: int, amount: int = ...): ...

    def media_pk_from_url(self, url: str) -> str: ...

    def realtime_connect(self, transport: object = ...) -> object: ...

    def realtime_on(self, event: str, handler: Callable[[object], None]) -> None: ...


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


class _DictAttr:
    """Wrap a plain dict so it can be read via ``getattr`` (recursively).

    Realtime delivers a direct message as a JSON dict, not an instagrapi
    ``DirectMessage`` object — but the pipeline (``normalize_direct_message`` +
    ``extract_share``) reads message and media fields via ``getattr``. This thin
    adapter lets the SAME normalization/identity path consume a realtime payload
    with no special-casing: nested dicts become ``_DictAttr`` and lists of dicts
    become lists of ``_DictAttr`` (so ``generic_xma`` works), while a missing
    key returns the supplied default just like a real object.
    """

    __slots__ = ("_data",)

    def __init__(self, data: dict) -> None:
        self._data = data

    def __getattr__(self, name: str) -> object:
        try:
            value = self._data[name]
        except KeyError:
            raise AttributeError(name) from None
        return _wrap(value)


def _wrap(value: object) -> object:
    """Recursively wrap dicts/lists so attribute reads work on realtime data."""
    if isinstance(value, dict):
        return _DictAttr(value)
    if isinstance(value, list):
        return [_wrap(item) for item in value]
    return value


def realtime_message_to_dm(message: object) -> object:
    """Adapt a realtime "message" event payload to a DM-shaped object.

    instagrapi emits the ``"message"`` event as ``{"message": {<DM fields>}}``,
    where the inner dict carries the direct-message item fields (``item_id``,
    ``user_id``, ``item_type``, ``timestamp``, media, ...). This returns an
    object exposing the attributes ``normalize_direct_message`` reads, mapping
    the realtime ``item_id`` to the canonical ``id`` and wrapping nested
    dicts/lists so ``extract_share`` sees attribute-bearing media. Tolerates an
    already-flat payload (no ``message`` wrapper) and a ``DirectMessage``-like
    object passed straight through (returned unchanged).
    """
    if isinstance(message, dict):
        inner = message.get("message", message)
    else:
        inner = getattr(message, "message", None)
        if inner is None:
            return message  # already a DM-shaped object
    if not isinstance(inner, dict):
        return inner
    data = dict(inner)
    # Realtime carries the message id as ``item_id``; map it to canonical ``id``.
    if "id" not in data and "item_id" in data:
        data["id"] = data["item_id"]
    return _DictAttr(data)


def realtime_thread_id(message: object) -> str | None:
    """Best-effort extract the thread id from a realtime "message" payload."""
    inner: object
    if isinstance(message, dict):
        inner = message.get("message", message)
    else:
        inner = getattr(message, "message", message)
    if isinstance(inner, dict):
        thread_id = inner.get("thread_id")
    else:
        thread_id = getattr(inner, "thread_id", None)
    return str(thread_id) if thread_id is not None else None


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
            if self._load_persisted_session():
                return

            self._fresh_login()
            self._client.dump_settings(self._config.session_path)
        except ChallengeError as exc:
            self._pause_on_challenge(exc)

    def _load_persisted_session(self) -> bool:
        """Reuse a persisted session if one exists and loads cleanly.

        Returns True when the session was reused (no fresh login needed). A
        missing file, or a corrupt one that fails to load, returns False so the
        caller falls back to a fresh credential login (and re-persists a good
        session).
        """
        if not Path(self._config.session_path).exists():
            return False
        try:
            self._client.load_settings(self._config.session_path)
            return True
        except Exception:
            logger.warning(
                "could not load persisted session at %s; falling back to fresh login",
                self._config.session_path,
                exc_info=True,
            )
            return False

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

    # --- polling ---------------------------------------------------------
    def fetch_recent_messages(self, amount: int | None = None) -> list[NormalizedMessage]:
        """Fetch recent messages from the configured thread, oldest-first.

        Returns :class:`NormalizedMessage` objects (timestamps as epoch ints,
        usernames resolved from the thread's user list) ready for the pipeline.
        instagrapi returns newest-first, so the list is reversed: feeding
        oldest-first means the genuine FIRST share is recorded before its
        later duplicates. Refuses while paused; spaced by a human-like delay.
        """
        self._ensure_active()
        self._delay()
        amount = amount if amount is not None else self._config.poll_amount
        thread = self._client.direct_thread(int(self._config.thread_id), amount=amount)

        users_by_id = {
            str(getattr(u, "pk", "")): str(getattr(u, "username", ""))
            for u in (getattr(thread, "users", None) or [])
        }
        messages = list(getattr(thread, "messages", None) or [])
        # Oldest-first so first-seen ordering matches real chronology.
        return [normalize_direct_message(dm, users_by_id) for dm in reversed(messages)]

    def poll_once(self, listener: Listener) -> None:
        """Fetch recent thread messages and feed each through the listener.

        This is the polling safety net: every fetched message goes through
        ``Listener.handle_message`` (the same idempotent gate realtime/backfill
        use), so duplicates produce exactly one reply and re-polled messages are
        skipped. Refuses to poll while paused.
        """
        for message in self.fetch_recent_messages():
            listener.handle_message(message)

    # --- realtime (MQTT) -------------------------------------------------
    def connect_realtime(self, listener: Listener) -> None:
        """Connect the MQTT realtime channel and wire it into the pipeline.

        Realtime is the PRIMARY low-latency source: it registers a ``"message"``
        handler (via ``realtime_on``) that, on a new direct message for the
        CONFIGURED thread, reuses ``normalize_direct_message`` and feeds
        ``Listener.handle_message`` — the SAME idempotent processed-message-id
        gate polling uses, so a message seen by both sources yields exactly one
        reply. Then it ``realtime_connect``s the transport (instagrapi delivers
        events on its own background thread thereafter).

        Refuses while paused (a challenge needs a human first). Handler errors
        are caught and logged so a single malformed realtime payload never tears
        down the connection — polling remains the dependable safety net.
        """
        self._ensure_active()
        self._client.realtime_on("message", self._make_realtime_handler(listener))
        self._client.realtime_connect()

    def _make_realtime_handler(self, listener: Listener) -> Callable[[object], None]:
        """Build the ``"message"`` handler bound to this listener."""

        def _on_message(payload: object) -> None:
            self._handle_realtime_message(listener, payload)

        return _on_message

    def _handle_realtime_message(self, listener: Listener, payload: object) -> None:
        """Filter to the configured thread, normalize, feed the listener.

        A realtime payload for a different thread is ignored. Any error parsing
        or processing a single payload is swallowed (logged) so realtime stays
        up and polling keeps catching what realtime drops.
        """
        try:
            thread_id = realtime_thread_id(payload)
            if thread_id is not None and thread_id != str(self._config.thread_id):
                return
            dm = realtime_message_to_dm(payload)
            listener.handle_message(normalize_direct_message(dm))
        except Exception:
            logger.warning("failed to process realtime message; skipping", exc_info=True)
