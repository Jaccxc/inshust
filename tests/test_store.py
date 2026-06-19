"""Behavior of the SQLite-backed Store, against a real temp database file.

We assert round-trips and restart-safety through the public methods, never by
inspecting SQL/columns directly.
"""

from inshust.models import MEDIA_TYPE_CLIP, ShareEvent
from inshust.store import Store


def _event(pk="100", message_id="m1", username="alice", ts=1_700_000_000):
    from inshust.models import MediaKey

    return ShareEvent(
        media_key=MediaKey(pk=pk, shortcode="sc-" + pk, media_type=MEDIA_TYPE_CLIP),
        sender_username=username,
        sender_user_id="uid-" + username,
        message_id=message_id,
        timestamp=ts,
        thread_id="t-1",
        client_context="ctx-" + message_id,
    )


def test_unseen_media_returns_none(tmp_path):
    store = Store(str(tmp_path / "db.sqlite"))
    assert store.get_seen("nope") is None


def test_record_share_round_trips(tmp_path):
    store = Store(str(tmp_path / "db.sqlite"))
    rec = store.record_share(_event(pk="100", username="alice", ts=1_700_000_000))
    assert rec.media_pk == "100"
    assert rec.first_sender_username == "alice"
    assert rec.first_sent_at == 1_700_000_000
    assert rec.original_message_id == "m1"

    seen = store.get_seen("100")
    assert seen is not None
    assert seen.first_sender_username == "alice"
    assert seen.shortcode == "sc-100"
    assert seen.media_type == MEDIA_TYPE_CLIP
    assert seen.original_client_context == "ctx-m1"


def test_processed_message_set(tmp_path):
    store = Store(str(tmp_path / "db.sqlite"))
    assert store.is_processed("m1") is False
    store.mark_processed("m1")
    assert store.is_processed("m1") is True


def test_state_persists_across_reopen(tmp_path):
    path = str(tmp_path / "db.sqlite")
    store = Store(path)
    store.record_share(_event(pk="200", username="bob"))
    store.mark_processed("m1")
    store.close()

    reopened = Store(path)
    assert reopened.get_seen("200") is not None
    assert reopened.get_seen("200").first_sender_username == "bob"
    assert reopened.is_processed("m1") is True


def test_url_cache_miss_returns_none(tmp_path):
    store = Store(str(tmp_path / "db.sqlite"))
    assert store.get_cached_pk("https://www.instagram.com/reel/CzAbc/") is None


def test_url_cache_round_trips(tmp_path):
    store = Store(str(tmp_path / "db.sqlite"))
    store.cache_url_pk("https://www.instagram.com/reel/CzAbc/", "999", resolved_at=1_700_000_000)
    assert store.get_cached_pk("https://www.instagram.com/reel/CzAbc/") == "999"


def test_url_cache_keeps_first_resolution(tmp_path):
    # A url maps to one media; re-caching must not clobber the original pk.
    store = Store(str(tmp_path / "db.sqlite"))
    store.cache_url_pk("u1", "111", resolved_at=1000)
    store.cache_url_pk("u1", "222", resolved_at=2000)
    assert store.get_cached_pk("u1") == "111"


def test_url_cache_persists_across_reopen(tmp_path):
    path = str(tmp_path / "db.sqlite")
    store = Store(path)
    store.cache_url_pk("u1", "111", resolved_at=1000)
    store.close()
    reopened = Store(path)
    assert reopened.get_cached_pk("u1") == "111"


def test_record_share_is_idempotent_keeps_first(tmp_path):
    # If the same media pk is recorded twice, the first-seen record stands.
    store = Store(str(tmp_path / "db.sqlite"))
    store.record_share(_event(pk="300", username="alice", message_id="m1", ts=1000))
    store.record_share(_event(pk="300", username="carol", message_id="m2", ts=2000))
    seen = store.get_seen("300")
    assert seen.first_sender_username == "alice"
    assert seen.first_sent_at == 1000
    assert seen.original_message_id == "m1"
