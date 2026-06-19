"""One-shot history backfill (card #7) — a SEPARATE, manually-run entry point.

This is NOT the live bot. It is run by hand exactly once to pre-populate the
SQLite seen-index from the group thread's FULL existing history, then the
resulting db file is shipped to Fly. The live bot never backfills on start; it
only reads and extends this database.

The walk:

1. ``adapter.fetch_history`` paginates the configured thread's history via
   ``oldest_cursor`` with deliberately human-like pacing (the backfill delay
   range) and a consistent device/headers (the adapter's session/locale),
   honoring an OPTIONAL cap (``BACKFILL_MAX_MESSAGES``, blank = full history).
   It returns messages OLDEST-first, so the genuine first share of a reel is
   recorded before any later re-share.
2. Each historical message is fed through ``extract_share`` (pure media
   identity) + the :class:`~inshust.resolver.MediaResolver` (url-only xma shares
   resolve via the client + the url->pk cache) into a canonical media key.
3. That key + the message metadata becomes a :class:`ShareEvent` recorded via
   ``Store.record_share``. Because ``record_share`` is ``INSERT OR IGNORE`` on
   the media pk, re-running is idempotent (an already-recorded pk is left
   untouched) and the FIRST sharer always wins.

It sends NO replies — it deliberately does NOT go through
``Listener.handle_message`` (which would reply on duplicates). Non-media and
unresolvable messages are skipped silently.

Every test drives this with a mock client (scripted history pages) and a fake
clock; no network, no real login.
"""

from __future__ import annotations

import logging

from inshust.adapter import InstagramAdapter
from inshust.config import Config, load_config
from inshust.logging_setup import (
    console_challenge_code_handler,
    console_two_factor_code_provider,
    setup_logging,
)
from inshust.media_identity import extract_share
from inshust.models import ShareEvent
from inshust.resolver import MediaResolver
from inshust.store import Store

logger = logging.getLogger(__name__)


class BackfillResult:
    """Observable summary of a backfill run (for logging + the entry point)."""

    __slots__ = ("messages_walked", "shares_recorded", "skipped")

    def __init__(self, messages_walked: int, shares_recorded: int, skipped: int) -> None:
        self.messages_walked = messages_walked
        self.shares_recorded = shares_recorded
        self.skipped = skipped


class Backfill:
    """Walk thread history into the store; record first-seen shares, no replies."""

    def __init__(self, config: Config, store: Store, adapter: InstagramAdapter) -> None:
        self._config = config
        self._store = store
        self._adapter = adapter
        self._resolver = MediaResolver(adapter, store)

    def run(self) -> BackfillResult:
        """Walk the full (or capped) history and record every share seen.

        Returns a :class:`BackfillResult` summarizing how many messages were
        walked, how many shares were recorded, and how many were skipped (non
        media or unresolvable). Idempotent across re-runs: ``record_share``
        ignores an already-known media pk, so the first sharer always stands.
        """
        messages = self._adapter.fetch_history(max_messages=self._config.backfill_max_messages)
        recorded = 0
        skipped = 0
        for message in messages:
            if self._record_one(message):
                recorded += 1
            else:
                skipped += 1
        logger.info(
            "backfill complete: walked=%d recorded=%d skipped=%d",
            len(messages),
            recorded,
            skipped,
        )
        return BackfillResult(
            messages_walked=len(messages),
            shares_recorded=recorded,
            skipped=skipped,
        )

    def _record_one(self, message: object) -> bool:
        """Record a single historical message's share; True if it was a share.

        Non-media messages and shares whose media cannot be resolved are skipped
        (returning False). The store's ``record_share`` is idempotent, so a media
        pk already recorded by an earlier (older) message is left untouched.
        """
        ref = extract_share(message)
        if ref is None:
            return False  # non-media: nothing to record
        media_key = self._resolver.resolve(ref)
        if media_key is None:
            logger.warning(
                "could not resolve historical share for message %s; skipping",
                getattr(message, "id", "?"),
            )
            return False
        event = ShareEvent(
            media_key=media_key,
            sender_username=str(getattr(message, "username", "")),
            sender_user_id=str(getattr(message, "user_id", "")),
            message_id=str(getattr(message, "id", "")),
            timestamp=int(getattr(message, "timestamp", 0)),
            thread_id=self._config.thread_id,
            client_context=getattr(message, "client_context", None),
        )
        self._store.record_share(event)
        return True


def main() -> None:  # pragma: no cover - thin console wiring, exercised via Backfill
    """Console entry point ``inshust-backfill``: run the one-shot backfill.

    Loads config, builds the real instagrapi-backed adapter, logs in once
    (reusing a persisted session when present), opens the SQLite store, and runs
    the backfill with human-like pacing. Sends no replies. Run this by hand once,
    then ship the resulting db file to Fly.
    """
    config = load_config()
    setup_logging(config.log_level)
    adapter = InstagramAdapter(
        config,
        challenge_code_handler=console_challenge_code_handler,
        two_factor_code_provider=console_two_factor_code_provider,
    )
    store = Store(config.db_path)
    logger.info(
        "inshust backfill starting: thread_id=%s db_path=%s cap=%s",
        config.thread_id or "<unset>",
        config.db_path,
        config.backfill_max_messages if config.backfill_max_messages is not None else "full",
    )
    adapter.login()
    result = Backfill(config, store, adapter).run()
    store.close()
    logger.info(
        "inshust backfill done: walked=%d recorded=%d skipped=%d",
        result.messages_walked,
        result.shares_recorded,
        result.skipped,
    )
