"""Orchestration: the single-message entry point that wires the pipeline.

``Listener.handle_message`` is the one seam every message source feeds. Realtime
(card #4) and polling (card #5) just call it; the backfill (card #6) reuses the
identity+store path without the reply. The flow:

1. idempotency gate — already processed? return (restart-safe, no double reply).
2. identify the share — non-media? mark processed and return (ignore silently).
3. resolve the share ref to a canonical MediaKey (url-only xma shares resolve
   via the client + url->pk cache). Unresolvable? mark processed and skip.
4. build a normalized ShareEvent from the resolved key + message metadata.
5. dedupe — FIRST_SEEN records silently; DUPLICATE sends exactly one reply.
6. always mark the message processed at the end.

Resolving before building the event is what makes the same reel dedupe to one
key whether it was shared as a clip or as an xma card.

The message is duck-typed: we read ``id``/``item_type``/``clip``/``media_share``
/the xma fields (via media identity) plus ``user_id``/``username``/``timestamp``/
``client_context`` here. This keeps tests free of real instagrapi objects.
"""

from __future__ import annotations

import logging

from inshust.client import ClientPort
from inshust.config import Config
from inshust.dedupe import dedupe
from inshust.media_identity import extract_share
from inshust.models import DUPLICATE, MediaKey, ShareEvent
from inshust.reply import format_reply
from inshust.resolver import MediaResolver
from inshust.store import Store

logger = logging.getLogger(__name__)


class Listener:
    """Wires config + store + client into the per-message pipeline."""

    def __init__(self, config: Config, store: Store, client: ClientPort) -> None:
        self._config = config
        self._store = store
        self._client = client
        self._resolver = MediaResolver(client, store)

    def handle_message(self, message: object) -> None:
        """Process exactly one incoming message (idempotent, restart-safe)."""
        message_id = str(getattr(message, "id", None))

        # 1. idempotency / restart safety
        if self._store.is_processed(message_id):
            return

        # 2. identify the share
        ref = extract_share(message)
        if ref is None:
            # non-media: ignore silently, but record so we never reconsider it
            self._store.mark_processed(message_id)
            return

        # 3. resolve to a canonical MediaKey (clip==xma dedupe to one key)
        media_key = self._resolver.resolve(ref)
        if media_key is None:
            # resolution failed (e.g. unresolvable xma url): skip gracefully,
            # mark processed so we don't retry it forever, and never crash.
            logger.warning("could not resolve share for message %s; skipping", message_id)
            self._store.mark_processed(message_id)
            return

        # 4. normalize into a ShareEvent
        event = self._build_event(message, media_key, message_id)

        # 5. dedupe + (on duplicate) reply exactly once
        decision = dedupe(self._store, event)
        if decision.kind == DUPLICATE and decision.original is not None:
            text = format_reply(decision.original, self._config.timezone)
            self._client.send_reply(
                thread_id=event.thread_id,
                text=text,
                original_message_id=event.message_id,
                original_client_context=event.client_context,
            )

        # 6. always mark processed last
        self._store.mark_processed(message_id)

    def _build_event(self, message: object, media_key: MediaKey, message_id: str) -> ShareEvent:
        return ShareEvent(
            media_key=media_key,
            sender_username=str(getattr(message, "username", "")),
            sender_user_id=str(getattr(message, "user_id", "")),
            message_id=message_id,
            timestamp=int(getattr(message, "timestamp", 0)),
            thread_id=self._config.thread_id,
            client_context=getattr(message, "client_context", None),
        )
