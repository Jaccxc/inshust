"""Orchestration: the single-message entry point that wires the pipeline.

``Listener.handle_message`` is the one seam every message source feeds. Realtime
(card #4) and polling (card #5) just call it; the backfill (card #6) reuses the
identity+store path without the reply. The flow:

1. idempotency gate — already processed? return (restart-safe, no double reply).
2. identify the share — non-media? mark processed and return (ignore silently).
3. resolve the share ref to a canonical MediaKey (url-only xma shares resolve
   via the client + url->pk cache). Unresolvable? mark processed and skip.
4. build a normalized ShareEvent from the resolved key + message metadata.
5. dedupe — FIRST_SEEN records silently; DUPLICATE sends exactly one reply
   (except when the message *is* the canonical first share — e.g. poll replay
   after backfill — which stays silent).
6. always mark the message processed at the end.

Card #6 (observability) adds STRUCTURED logs at this orchestration seam: each
observed share (``event=share_observed``), the dedupe decision
(``event=dedupe_decision`` with FIRST_SEEN/DUPLICATE), and the reply outcome
(``event=reply_outcome`` with sent/fallback/failed). A persistent send failure
(``SendFailedError`` from the adapter after its retry budget) is caught here and
logged as a ``failed`` outcome so the run loop survives instead of crashing.

Resolving before building the event is what makes the same reel dedupe to one
key whether it was shared as a clip or as an xma card.

The message is duck-typed: we read ``id``/``item_type``/``clip``/``media_share``
/the xma fields (via media identity) plus ``user_id``/``username``/``timestamp``/
``client_context`` here. This keeps tests free of real instagrapi objects.
"""

from __future__ import annotations

import logging

from inshust.adapter import SendFailedError
from inshust.client import ClientPort
from inshust.config import Config
from inshust.dedupe import dedupe
from inshust.demerits import breakdown, classify
from inshust.media_identity import extract_share
from inshust.models import DUPLICATE, MediaKey, SeenRecord, ShareEvent
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

        # 1b. command system (text): ~clear (admin only) / ~me (anyone)
        if self._maybe_command(message, message_id):
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
        logger.info(
            "observed share",
            extra={
                "event": "share_observed",
                "message_id": event.message_id,
                "media_pk": event.media_key.pk,
                "media_type": event.media_key.media_type,
                "sender_username": event.sender_username,
                "thread_id": event.thread_id,
            },
        )

        # 5. dedupe + (on duplicate) reply exactly once
        decision = dedupe(self._store, event)
        logger.info(
            "dedupe decision %s",
            decision.kind,
            extra={
                "event": "dedupe_decision",
                "message_id": event.message_id,
                "media_pk": event.media_key.pk,
                "decision": decision.kind,
            },
        )
        if decision.kind == DUPLICATE and decision.original is not None:
            # Re-seeing the canonical first-share message (e.g. after backfill
            # populated seen_media, or a poll replay) is not a re-share.
            if event.message_id != decision.original.original_message_id:
                self._reply_to_duplicate(event, decision.original)

        # 6. always mark processed last
        self._store.mark_processed(message_id)

    def _maybe_command(self, message: object, message_id: str) -> bool:
        """Handle a ``~clear`` / ``~me`` text command. Returns True if handled.

        ``~clear`` wipes every warning record, but only when sent by the
        configured admin username (a non-admin ``~clear`` is silently ignored).
        Anyone may ``~me`` to query their own standing. A handled command is
        marked processed and never flows into the dedupe path.
        """
        text = getattr(message, "text", None)
        if not text:
            return False
        cmd = text.strip().lower()
        if cmd not in ("~clear", "~me"):
            return False
        username = str(getattr(message, "username", ""))
        if cmd == "~clear":
            if username == self._config.admin_username:
                removed = self._store.clear_warnings()
                self._command_reply(message, f"已清除所有人的警告紀錄（共 {removed} 筆）")
                logger.info(
                    "warnings cleared",
                    extra={
                        "event": "command",
                        "command": "clear",
                        "by": username,
                        "removed": removed,
                    },
                )
            # a non-admin ~clear is ignored: no clear, no reply.
        else:  # ~me
            total = self._store.total_warnings(username)
            self._command_reply(message, f"@{username} 目前累計：{breakdown(total)}")
        self._store.mark_processed(message_id)
        return True

    def _command_reply(self, message: object, text: str) -> None:
        """Reply to a command message (quoting it), surviving a send failure."""
        try:
            self._client.send_reply(
                thread_id=self._config.thread_id,
                text=text,
                original_message_id=str(getattr(message, "id", "")),
                original_client_context=getattr(message, "client_context", None),
            )
        except SendFailedError:
            logger.error(
                "command reply failed", extra={"event": "command_reply", "outcome": "failed"}
            )

    def _reply_to_duplicate(self, event: ShareEvent, original: SeenRecord) -> None:
        """Send the duplicate call-out (with its demerit) and record the warning.

        Quote-replies attach to the canonical first-share message (``original``),
        not the duplicate. A staleness-based penalty (see :mod:`inshust.demerits`)
        is appended to the reply — e.g. ``…慢了 506 天，記大過五支`` — and recorded
        against the re-sharer in 警告 units.

        The adapter retries transient group-send 500s internally; a PERSISTENT
        failure surfaces as :class:`SendFailedError`, caught here and logged as a
        ``"failed"`` outcome so the run loop survives. The demerit is recorded
        regardless of the send outcome (the repetition happened).
        """
        gap = event.timestamp - original.first_sent_at
        penalty = classify(gap)
        text = format_reply(
            original,
            duplicate_username=event.sender_username,
            duplicate_sent_at=event.timestamp,
            tz=self._config.timezone,
        )
        text = f"{text}，記{penalty.label}"
        try:
            outcome = self._client.send_reply(
                thread_id=event.thread_id,
                text=text,
                original_message_id=original.original_message_id,
                original_client_context=original.original_client_context,
            )
        except SendFailedError:
            logger.error(
                "reply send failed after retries",
                extra={
                    "event": "reply_outcome",
                    "message_id": event.message_id,
                    "media_pk": event.media_key.pk,
                    "outcome": "failed",
                },
            )
        else:
            logger.info(
                "reply sent",
                extra={
                    "event": "reply_outcome",
                    "message_id": event.message_id,
                    "media_pk": event.media_key.pk,
                    "outcome": outcome or "sent",
                },
            )
        # record the demerit against the re-sharer (in 警告 units), always.
        self._store.add_warning(
            event.sender_username,
            penalty.warnings,
            media_pk=event.media_key.pk,
            gap_seconds=max(0, gap),
            occurred_at=event.timestamp,
        )

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
