"""SQLite persistence for the seen-index and processed-message set.

Wraps the stdlib ``sqlite3`` directly (rule 5): real typed columns, TEXT enum
fields validated in the app layer, timestamps as epoch ``INTEGER``, and indices
on the lookup columns. The schema is created on open; reopening the same file
preserves data, so the bot is restart-safe.

This slice owns two tables:

- ``seen_media`` — one row per media pk (first-seen share).
- ``processed_messages`` — the idempotency gate of handled message ids.

Later cards extend this (e.g. the xma URL->pk cache, card #3) by adding tables
and methods; they do not rewrite these.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from inshust.models import SeenRecord, ShareEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_media (
    media_pk                TEXT    PRIMARY KEY,
    shortcode               TEXT,
    media_type              TEXT    NOT NULL,
    first_sender_username   TEXT    NOT NULL,
    first_sender_user_id    TEXT    NOT NULL,
    first_sent_at           INTEGER NOT NULL,
    original_message_id     TEXT    NOT NULL,
    original_client_context TEXT,
    thread_id               TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS processed_messages (
    message_id   TEXT    PRIMARY KEY,
    processed_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_seen_media_pk
    ON seen_media (media_pk);
CREATE INDEX IF NOT EXISTS idx_processed_message_id
    ON processed_messages (message_id);
"""


class Store:
    """Encapsulates all persistence behind a small typed interface."""

    def __init__(self, db_path: str) -> None:
        # Ensure the parent directory exists for the default ./data/... path.
        parent = Path(db_path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # --- seen-index ------------------------------------------------------
    def get_seen(self, media_pk: str) -> SeenRecord | None:
        """Return the first-seen record for ``media_pk``, or ``None``."""
        row = self._conn.execute(
            "SELECT * FROM seen_media WHERE media_pk = ?",
            (media_pk,),
        ).fetchone()
        return self._row_to_seen(row) if row is not None else None

    def record_share(self, event: ShareEvent) -> SeenRecord:
        """Persist ``event`` as the first-seen share for its media pk.

        Idempotent: if a row already exists for the pk it is left untouched and
        the existing record is returned. The caller (dedupe) decides FIRST_SEEN
        vs DUPLICATE before calling, so a clash here is a benign race.
        """
        key = event.media_key
        self._conn.execute(
            """
            INSERT OR IGNORE INTO seen_media (
                media_pk, shortcode, media_type,
                first_sender_username, first_sender_user_id, first_sent_at,
                original_message_id, original_client_context, thread_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key.pk,
                key.shortcode,
                key.media_type,
                event.sender_username,
                event.sender_user_id,
                event.timestamp,
                event.message_id,
                event.client_context,
                event.thread_id,
            ),
        )
        self._conn.commit()
        # Return whatever now stands as the canonical first-seen record.
        return self.get_seen(key.pk)  # type: ignore[return-value]

    # --- processed-message gate -----------------------------------------
    def is_processed(self, message_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM processed_messages WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        return row is not None

    def mark_processed(self, message_id: str, processed_at: int = 0) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO processed_messages (message_id, processed_at) VALUES (?, ?)",
            (message_id, processed_at),
        )
        self._conn.commit()

    # --- lifecycle -------------------------------------------------------
    def close(self) -> None:
        self._conn.close()

    # --- helpers ---------------------------------------------------------
    @staticmethod
    def _row_to_seen(row: sqlite3.Row) -> SeenRecord:
        return SeenRecord(
            media_pk=row["media_pk"],
            shortcode=row["shortcode"],
            media_type=row["media_type"],
            first_sender_username=row["first_sender_username"],
            first_sender_user_id=row["first_sender_user_id"],
            first_sent_at=row["first_sent_at"],
            original_message_id=row["original_message_id"],
            original_client_context=row["original_client_context"],
            thread_id=row["thread_id"],
        )
