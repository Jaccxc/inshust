"""Behavior of the core dedupe decision, against a real temp store.

We assert the FIRST_SEEN/DUPLICATE decision and the side effect (recorded vs
not) through the public store, not internals.
"""

from inshust.dedupe import dedupe
from inshust.models import DUPLICATE, FIRST_SEEN, MEDIA_TYPE_CLIP, MediaKey, ShareEvent
from inshust.store import Store


def _event(pk="100", message_id="m1", username="alice", ts=1_700_000_000):
    return ShareEvent(
        media_key=MediaKey(pk=pk, shortcode="sc", media_type=MEDIA_TYPE_CLIP),
        sender_username=username,
        sender_user_id="uid",
        message_id=message_id,
        timestamp=ts,
        thread_id="t-1",
        client_context="ctx",
    )


def test_first_share_is_first_seen_and_recorded(tmp_path):
    store = Store(str(tmp_path / "db.sqlite"))
    decision = dedupe(store, _event(pk="100", username="alice"))
    assert decision.kind == FIRST_SEEN
    assert decision.original is None
    assert store.get_seen("100") is not None


def test_repeat_share_is_duplicate_with_original(tmp_path):
    store = Store(str(tmp_path / "db.sqlite"))
    dedupe(store, _event(pk="100", username="alice", message_id="m1", ts=1000))
    decision = dedupe(store, _event(pk="100", username="bob", message_id="m2", ts=2000))
    assert decision.kind == DUPLICATE
    assert decision.original is not None
    assert decision.original.first_sender_username == "alice"
    assert decision.original.first_sent_at == 1000


def test_different_media_are_independent(tmp_path):
    store = Store(str(tmp_path / "db.sqlite"))
    d1 = dedupe(store, _event(pk="100"))
    d2 = dedupe(store, _event(pk="200"))
    assert d1.kind == FIRST_SEEN
    assert d2.kind == FIRST_SEEN
