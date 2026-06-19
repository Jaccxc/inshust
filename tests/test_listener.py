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
        user_id="uid-x",
        username="x",
        timestamp=1_700_000_000,
        client_context=None,
    )


def _make(tmp_path):
    cfg = _config(tmp_path)
    store = Store(cfg.db_path)
    client = FakeClient()
    return Listener(cfg, store, client), store, client


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
