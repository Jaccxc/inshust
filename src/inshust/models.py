"""Domain value objects for the dedupe pipeline.

Enum-like fields (``kind``, ``media_type``) are plain ``str`` TEXT constants per
the storage rule, never magic ints. These types are deliberately small and
immutable so the pure modules (media identity, dedupe, reply) and the store can
pass them around without coupling to instagrapi objects.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- media_type constants (TEXT) -------------------------------------------
# ``clip`` and ``feed`` carry a full Media object with a stable ``pk``/``code``.
# ``xma`` is the rich URL-only share card (xma_share/generic_xma): its ShareRef
# carries a ``url`` with ``pk``/``shortcode`` left ``None`` until the resolver
# turns the url into a pk (card #4).
MEDIA_TYPE_CLIP = "clip"
MEDIA_TYPE_FEED = "feed"
MEDIA_TYPE_XMA = "xma"

# --- DedupeDecision.kind constants (TEXT) ----------------------------------
FIRST_SEEN = "FIRST_SEEN"
DUPLICATE = "DUPLICATE"


@dataclass(frozen=True)
class MediaKey:
    """Stable identity of a shared reel/post.

    ``pk`` is the canonical dedupe key; ``shortcode`` is the human-facing code
    (may be ``None`` for forms that only expose a pk). ``media_type`` is a TEXT
    constant describing the share form.
    """

    pk: str
    shortcode: str | None
    media_type: str


@dataclass(frozen=True)
class ShareEvent:
    """A normalized incoming share, ready for the dedupe decision."""

    media_key: MediaKey
    sender_username: str
    sender_user_id: str
    message_id: str
    timestamp: int  # epoch seconds (UTC)
    thread_id: str
    client_context: str | None


@dataclass(frozen=True)
class SeenRecord:
    """The persisted first-seen share for a given media pk."""

    media_pk: str
    shortcode: str | None
    media_type: str
    first_sender_username: str
    first_sender_user_id: str
    first_sent_at: int  # epoch seconds (UTC)
    original_message_id: str
    original_client_context: str | None
    thread_id: str


@dataclass(frozen=True)
class DedupeDecision:
    """Result of running an incoming :class:`ShareEvent` through dedupe."""

    kind: str  # FIRST_SEEN | DUPLICATE
    original: SeenRecord | None
