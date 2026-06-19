"""End-to-end integration of the walking skeleton via the FakeClient.

Covers every acceptance criterion of card #2 behaviorally: one reply per
duplicate naming the first sender + localized time, silent first-seen,
non-media ignored, message-id idempotency, and restart-safety across reopening
the same SQLite file. No network.
"""

from types import SimpleNamespace

from inshust.client import FakeClient
from inshust.config import Config
from inshust.listener import Listener
from inshust.store import Store


def _config(tmp_path):
    return Config(thread_id="t-1", db_path=str(tmp_path / "db.sqlite"), timezone="Asia/Taipei")


def _share_msg(
    message_id,
    pk,
    *,
    username="alice",
    user_id="uid-alice",
    item_type="clip",
    code="CzAbc",
    timestamp=1_700_000_000,
    client_context="ctx",
):
    media = SimpleNamespace(pk=pk, code=code)
    return SimpleNamespace(
        id=message_id,
        item_type=item_type,
        clip=media if item_type == "clip" else None,
        media_share=media if item_type == "media_share" else None,
        user_id=user_id,
        username=username,
        timestamp=timestamp,
        client_context=client_context,
    )


def _text_msg(message_id, item_type="text"):
    return SimpleNamespace(
        id=message_id,
        item_type=item_type,
        clip=None,
        media_share=None,
        xma_share=None,
        generic_xma=None,
        user_id="uid-x",
        username="x",
        timestamp=1_700_000_000,
        client_context=None,
    )


def _xma_msg(
    message_id,
    url,
    *,
    username="bob",
    user_id="uid-bob",
    item_type="xma_share",
    timestamp=1_700_100_000,
    client_context="ctx-xma",
):
    xma = SimpleNamespace(video_url=url)
    return SimpleNamespace(
        id=message_id,
        item_type=item_type,
        clip=None,
        media_share=None,
        xma_share=xma if item_type != "generic_xma" else None,
        generic_xma=[xma] if item_type == "generic_xma" else None,
        user_id=user_id,
        username=username,
        timestamp=timestamp,
        client_context=client_context,
    )


def _make(tmp_path, *, url_to_pk=None):
    cfg = _config(tmp_path)
    store = Store(cfg.db_path)
    client = FakeClient(url_to_pk=url_to_pk or {})
    return Listener(cfg, store, client), store, client


from inshust.models import MEDIA_TYPE_CLIP, MediaKey, ShareEvent


def test_reseeing_first_share_recorded_by_backfill_is_silent(tmp_path):
    cfg = _config(tmp_path)
    store = Store(cfg.db_path)
    store.record_share(
        ShareEvent(
            media_key=MediaKey(pk="100", shortcode="CzAbc", media_type=MEDIA_TYPE_CLIP),
            sender_username="alice",
            sender_user_id="uid-alice",
            message_id="m1",
            timestamp=1_700_000_000,
            thread_id=cfg.thread_id,
            client_context="ctx",
        )
    )
    client = FakeClient()
    listener = Listener(cfg, store, client)
    listener.handle_message(_share_msg("m1", pk="100", username="alice"))
    assert client.sent == []


def test_two_identical_shares_yield_exactly_one_reply(tmp_path):
    listener, _store, client = _make(tmp_path)
    listener.handle_message(_share_msg("m1", pk="100", username="alice", timestamp=1_700_000_000))
    listener.handle_message(_share_msg("m2", pk="100", username="bob", timestamp=1_700_100_000))
    assert len(client.sent) == 1
    reply = client.sent[0]
    assert "@alice" in reply.text  # first sender, not the re-sharer
    assert "2023-11-15 06:13" in reply.text  # localized first-seen time
    assert reply.original_message_id == "m2"  # quotes the new (duplicate) share


def test_first_seen_is_silent(tmp_path):
    listener, store, client = _make(tmp_path)
    listener.handle_message(_share_msg("m1", pk="100"))
    assert client.sent == []
    assert store.get_seen("100") is not None


def test_non_media_messages_are_ignored(tmp_path):
    listener, store, client = _make(tmp_path)
    listener.handle_message(_text_msg("m1", item_type="text"))
    listener.handle_message(_text_msg("m2", item_type="link"))
    assert client.sent == []
    # ignored but still marked processed so they are not reconsidered
    assert store.is_processed("m1") is True
    assert store.is_processed("m2") is True


def test_same_message_id_twice_no_second_reply(tmp_path):
    listener, _store, client = _make(tmp_path)
    listener.handle_message(_share_msg("m1", pk="100", username="alice"))
    # A duplicate share under a NEW id replies once...
    listener.handle_message(_share_msg("m2", pk="100", username="bob"))
    assert len(client.sent) == 1
    # ...replaying the SAME id must not reply again.
    listener.handle_message(_share_msg("m2", pk="100", username="bob"))
    assert len(client.sent) == 1


def test_state_persists_across_restart_no_re_reply(tmp_path):
    cfg = _config(tmp_path)
    client1 = FakeClient()
    store1 = Store(cfg.db_path)
    Listener(cfg, store1, client1).handle_message(_share_msg("m1", pk="100", username="alice"))
    Listener(cfg, store1, client1).handle_message(_share_msg("m2", pk="100", username="bob"))
    assert len(client1.sent) == 1
    store1.close()

    # Simulated restart: brand-new client + store over the same file.
    client2 = FakeClient()
    store2 = Store(cfg.db_path)
    listener2 = Listener(cfg, store2, client2)
    # Replaying an already-processed id -> no reply.
    listener2.handle_message(_share_msg("m2", pk="100", username="bob"))
    assert client2.sent == []
    # A genuinely new duplicate share still replies once, naming alice.
    listener2.handle_message(_share_msg("m3", pk="100", username="carol"))
    assert len(client2.sent) == 1
    assert "@alice" in client2.sent[0].text


# --- card #4: xma/generic share resolution + url->pk cache -----------------

_REEL_URL = "https://www.instagram.com/reel/CzAbc/"


def test_clip_then_same_reel_as_xma_dedupes_to_one_reply(tmp_path):
    # The same reel resolves to pk "100" whether shared as a clip or an xma.
    listener, _store, client = _make(tmp_path, url_to_pk={_REEL_URL: "100"})
    listener.handle_message(_share_msg("m1", pk="100", username="alice", timestamp=1_700_000_000))
    listener.handle_message(_xma_msg("m2", _REEL_URL, username="bob", timestamp=1_700_100_000))
    assert len(client.sent) == 1  # recognized as a duplicate of the clip
    reply = client.sent[0]
    assert "@alice" in reply.text  # the original (clip) sender
    assert reply.original_message_id == "m2"  # quotes the new xma share


def test_xma_first_then_same_reel_as_clip_dedupes(tmp_path):
    # Order-independent: xma first, clip second still dedupes to one key.
    listener, _store, client = _make(tmp_path, url_to_pk={_REEL_URL: "100"})
    listener.handle_message(_xma_msg("m1", _REEL_URL, username="alice", timestamp=1_700_000_000))
    listener.handle_message(_share_msg("m2", pk="100", username="bob", timestamp=1_700_100_000))
    assert len(client.sent) == 1
    assert "@alice" in client.sent[0].text


def test_repeated_xma_url_uses_cache_no_second_client_call(tmp_path):
    listener, _store, client = _make(tmp_path, url_to_pk={_REEL_URL: "100"})
    listener.handle_message(_xma_msg("m1", _REEL_URL, username="alice"))
    listener.handle_message(_xma_msg("m2", _REEL_URL, username="bob"))
    assert client.resolve_calls == 1  # second xma hit the store cache
    assert len(client.sent) == 1  # and was still recognized as a duplicate


def test_unresolvable_xma_is_skipped_gracefully(tmp_path):
    # No url_to_pk entry -> client raises -> resolver returns None.
    listener, store, client = _make(tmp_path, url_to_pk={})
    # Must not crash; no reply; nothing recorded as seen; message marked processed.
    listener.handle_message(_xma_msg("m1", _REEL_URL, username="alice"))
    assert client.sent == []
    assert store.is_processed("m1") is True


def test_unresolvable_xma_does_not_block_later_valid_shares(tmp_path):
    listener, _store, client = _make(tmp_path, url_to_pk={_REEL_URL: "100"})
    # A bad xma (unknown url) is skipped...
    listener.handle_message(_xma_msg("m1", "https://www.instagram.com/reel/UNKNOWN/"))
    assert client.sent == []
    # ...and a subsequent clip + xma of a resolvable reel still dedupe.
    listener.handle_message(_share_msg("m2", pk="100", username="alice"))
    listener.handle_message(_xma_msg("m3", _REEL_URL, username="bob"))
    assert len(client.sent) == 1
    assert "@alice" in client.sent[0].text
