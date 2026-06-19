"""Behavior of the domain types.

We assert the public shape other modules and tests rely on: constructing the
value objects and reading their fields. Enum-like fields are plain TEXT/str
constants (rule 5), not ints.
"""

from inshust.models import (
    DUPLICATE,
    FIRST_SEEN,
    MEDIA_TYPE_CLIP,
    MEDIA_TYPE_FEED,
    DedupeDecision,
    MediaKey,
    ShareEvent,
)


def test_media_key_carries_pk_shortcode_and_type():
    key = MediaKey(pk="123", shortcode="abc", media_type=MEDIA_TYPE_CLIP)
    assert key.pk == "123"
    assert key.shortcode == "abc"
    assert key.media_type == MEDIA_TYPE_CLIP


def test_share_event_holds_a_media_key_and_sender_context():
    key = MediaKey(pk="123", shortcode="abc", media_type=MEDIA_TYPE_FEED)
    event = ShareEvent(
        media_key=key,
        sender_username="alice",
        sender_user_id="42",
        message_id="mid-1",
        timestamp=1_700_000_000,
        thread_id="t-1",
        client_context="ctx-1",
    )
    assert event.media_key is key
    assert event.sender_username == "alice"
    assert event.timestamp == 1_700_000_000
    assert event.client_context == "ctx-1"


def test_dedupe_decision_kinds_are_text_constants():
    assert FIRST_SEEN == "FIRST_SEEN"
    assert DUPLICATE == "DUPLICATE"
    decision = DedupeDecision(kind=FIRST_SEEN, original=None)
    assert decision.kind == "FIRST_SEEN"
    assert decision.original is None
