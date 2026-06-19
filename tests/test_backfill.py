"""Behavior of the one-shot history backfill (card #7).

The backfill is a SEPARATE, manually-run entry point. It walks the configured
group thread's full history (or up to an optional cap) with human-like pacing
and feeds each historical message through media identity + the resolver into the
store. It sends NO replies and re-running is idempotent.

Every test uses a mock client (scripted history pages) + a fake clock asserting
pacing. No network, no real instagrapi login. We assert observable behavior
through public interfaces: store population, cap handling, idempotency, and that
no reply was ever sent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from inshust.adapter import InstagramAdapter
from inshust.backfill import Backfill
from inshust.config import Config
from inshust.store import Store


def _config(tmp_path, **overrides):
    base = dict(
        thread_id="42",
        db_path=str(tmp_path / "db.sqlite"),
        timezone="Asia/Taipei",
        username="bot",
        password="pw",
        session_path=str(tmp_path / "session.json"),
        backfill_delay_min_seconds=4.0,
        backfill_delay_max_seconds=4.0,
    )
    base.update(overrides)
    return Config(**base)


def _dm(message_id, *, user_id, item_type, ts, clip_pk=None, xma_url=None, ctx="ctx"):
    clip = SimpleNamespace(pk=clip_pk, code="CODE") if clip_pk is not None else None
    media_share = (
        SimpleNamespace(pk=clip_pk, code="CODE")
        if (clip_pk is not None and item_type == "media_share")
        else None
    )
    xma = SimpleNamespace(url=xma_url, video_url=xma_url) if xma_url is not None else None
    return SimpleNamespace(
        id=message_id,
        user_id=user_id,
        item_type=item_type,
        timestamp=datetime.fromtimestamp(ts, tz=UTC),
        client_context=ctx,
        clip=clip if item_type == "clip" else None,
        media_share=media_share,
        xma_share=xma if item_type in ("xma_share", "generic_xma") else None,
        generic_xma=None,
    )


def _page(messages, users, *, oldest_cursor):
    return SimpleNamespace(
        id="42",
        messages=list(messages),
        users=[SimpleNamespace(pk=pk, username=name) for pk, name in users.items()],
        oldest_cursor=oldest_cursor,
    )


def _adapter(cfg, client):
    sleeps: list[float] = []
    adapter = InstagramAdapter(cfg, client=client, sleeper=sleeps.append)
    adapter.recorded_sleeps = sleeps  # type: ignore[attr-defined]
    return adapter


# --- store population from clip / media_share / xma_share ------------------


def test_backfill_records_clip_media_share_and_xma_shares(tmp_path):
    cfg = _config(tmp_path)
    client = MagicMock()
    # A single page mixing all three share forms plus a non-media text message.
    # xma needs the resolver: scripted url -> pk via the client.
    client.media_pk_from_url.return_value = "300"
    page = _page(
        [
            _dm("m4", user_id="uid-bob", item_type="text", ts=1_700_400_000),
            _dm(
                "m3",
                user_id="uid-carol",
                item_type="xma_share",
                ts=1_700_300_000,
                xma_url="https://www.instagram.com/reel/Zzz/",
            ),
            _dm("m2", user_id="uid-bob", item_type="media_share", ts=1_700_200_000, clip_pk="200"),
            _dm("m1", user_id="uid-alice", item_type="clip", ts=1_700_100_000, clip_pk="100"),
        ],
        users={"uid-alice": "alice", "uid-bob": "bob", "uid-carol": "carol"},
        oldest_cursor=None,
    )
    client.direct_thread.return_value = page

    store = Store(cfg.db_path)
    adapter = _adapter(cfg, client)
    Backfill(cfg, store, adapter).run()

    # All three media shares are recorded in the seen-index, keyed by media pk.
    clip = store.get_seen("100")
    feed = store.get_seen("200")
    xma = store.get_seen("300")
    assert clip is not None and clip.first_sender_username == "alice"
    assert feed is not None and feed.first_sender_username == "bob"
    assert xma is not None and xma.first_sender_username == "carol"
    # The non-media text message produced no seen record.
    # (only the three pks above exist; nothing else to assert directly)


def test_backfill_logs_skip_reasons_for_non_media(tmp_path, caplog):
    cfg = _config(tmp_path)
    client = MagicMock()
    page = _page(
        [
            _dm("m2", user_id="uid-bob", item_type="text", ts=1_700_200_000),
            _dm("m1", user_id="uid-alice", item_type="clip", ts=1_700_100_000, clip_pk="100"),
        ],
        users={"uid-alice": "alice", "uid-bob": "bob"},
        oldest_cursor=None,
    )
    client.direct_thread.return_value = page

    store = Store(cfg.db_path)
    adapter = _adapter(cfg, client)
    with caplog.at_level("INFO", logger="inshust.backfill"):
        Backfill(cfg, store, adapter).run()

    skip_logs = [r for r in caplog.records if "backfill skip:" in r.message]
    record_logs = [r for r in caplog.records if "backfill record:" in r.message]
    assert len(skip_logs) == 1
    assert "not a reel/post share (item_type=text)" in skip_logs[0].message
    assert "m2" in skip_logs[0].message
    assert len(record_logs) == 1
    assert "media_pk=100" in record_logs[0].message


def test_backfill_marks_walked_messages_processed(tmp_path):
    cfg = _config(tmp_path)
    client = MagicMock()
    page = _page(
        [
            _dm("m2", user_id="uid-bob", item_type="text", ts=1_700_200_000),
            _dm("m1", user_id="uid-alice", item_type="clip", ts=1_700_100_000, clip_pk="100"),
        ],
        users={"uid-alice": "alice", "uid-bob": "bob"},
        oldest_cursor=None,
    )
    client.direct_thread.return_value = page

    store = Store(cfg.db_path)
    Backfill(cfg, store, _adapter(cfg, client)).run()

    assert store.is_processed("m1")
    assert store.is_processed("m2")


def test_backfill_sends_no_replies(tmp_path):
    cfg = _config(tmp_path)
    client = MagicMock()
    # The SAME reel shared twice in history would be a "duplicate" for the live
    # bot — the backfill must NOT reply, it only records the first-seen share.
    page = _page(
        [
            _dm("m2", user_id="uid-bob", item_type="clip", ts=1_700_200_000, clip_pk="100"),
            _dm("m1", user_id="uid-alice", item_type="clip", ts=1_700_100_000, clip_pk="100"),
        ],
        users={"uid-alice": "alice", "uid-bob": "bob"},
        oldest_cursor=None,
    )
    client.direct_thread.return_value = page

    store = Store(cfg.db_path)
    adapter = _adapter(cfg, client)
    Backfill(cfg, store, adapter).run()

    # Absolutely no send was ever attempted.
    client.direct_send.assert_not_called()
    # The first (oldest) sharer is recorded as first-seen.
    seen = store.get_seen("100")
    assert seen is not None
    assert seen.first_sender_username == "alice"


# --- human-like pacing via the fake clock ----------------------------------


def test_backfill_uses_human_like_pacing_between_requests(tmp_path):
    cfg = _config(tmp_path, backfill_delay_min_seconds=4.0, backfill_delay_max_seconds=4.0)
    client = MagicMock()
    page1 = _page(
        [_dm("m2", user_id="uid-bob", item_type="clip", ts=1_700_200_000, clip_pk="200")],
        users={"uid-bob": "bob"},
        oldest_cursor="cur-1",
    )
    page2 = _page(
        [_dm("m1", user_id="uid-alice", item_type="clip", ts=1_700_100_000, clip_pk="100")],
        users={"uid-alice": "alice"},
        oldest_cursor=None,
    )
    client.direct_thread.side_effect = [page1, page2]

    store = Store(cfg.db_path)
    adapter = _adapter(cfg, client)
    Backfill(cfg, store, adapter).run()

    # A human-like delay was requested between page requests (fake clock).
    assert adapter.recorded_sleeps
    assert all(s == 4.0 for s in adapter.recorded_sleeps)


# --- optional cap honored --------------------------------------------------


def test_backfill_honors_optional_message_cap(tmp_path):
    cfg = _config(tmp_path, backfill_max_messages=1)
    client = MagicMock()
    page1 = _page(
        [
            _dm("m3", user_id="uid-bob", item_type="clip", ts=1_700_300_000, clip_pk="300"),
            _dm("m2", user_id="uid-bob", item_type="clip", ts=1_700_200_000, clip_pk="200"),
        ],
        users={"uid-bob": "bob"},
        oldest_cursor="cur-1",
    )
    page2 = _page(
        [_dm("m1", user_id="uid-alice", item_type="clip", ts=1_700_100_000, clip_pk="100")],
        users={"uid-alice": "alice"},
        oldest_cursor=None,
    )
    client.direct_thread.side_effect = [page1, page2]

    store = Store(cfg.db_path)
    adapter = _adapter(cfg, client)
    Backfill(cfg, store, adapter).run()

    # Only one message was walked, so only one pk is in the seen-index.
    # The cap stopped pagination before the older page was fetched.
    assert client.direct_thread.call_count == 1
    recorded = [pk for pk in ("100", "200", "300") if store.get_seen(pk) is not None]
    assert len(recorded) == 1


# --- idempotent re-run, usable by the live bot -----------------------------


def test_backfill_rerun_is_idempotent(tmp_path):
    cfg = _config(tmp_path)
    client = MagicMock()
    page = _page(
        [
            _dm("m2", user_id="uid-bob", item_type="clip", ts=1_700_200_000, clip_pk="100"),
            _dm("m1", user_id="uid-alice", item_type="clip", ts=1_700_100_000, clip_pk="100"),
        ],
        users={"uid-alice": "alice", "uid-bob": "bob"},
        oldest_cursor=None,
    )
    # Two identical runs against the same db file.
    client.direct_thread.return_value = page

    store = Store(cfg.db_path)
    adapter = _adapter(cfg, client)
    Backfill(cfg, store, adapter).run()
    first = store.get_seen("100")
    Backfill(cfg, store, adapter).run()
    second = store.get_seen("100")

    # The first-seen record is unchanged across the re-run (alice, not bob).
    assert first is not None and second is not None
    assert second.first_sender_username == "alice"
    assert second.original_message_id == "m1"
    assert second == first


def test_backfilled_db_is_usable_by_the_live_listener(tmp_path):
    """The resulting SQLite file is directly usable by the live bot.

    After backfill records the first share of a reel, a LATER live re-share of
    the same reel must be recognized as a duplicate and trigger exactly one
    reply — with no further backfill. We reopen the same db file as the live
    listener would on restart.
    """
    from inshust.client import FakeClient
    from inshust.listener import Listener

    cfg = _config(tmp_path)
    client = MagicMock()
    page = _page(
        [_dm("m1", user_id="uid-alice", item_type="clip", ts=1_700_100_000, clip_pk="100")],
        users={"uid-alice": "alice"},
        oldest_cursor=None,
    )
    client.direct_thread.return_value = page

    store = Store(cfg.db_path)
    Backfill(cfg, store, _adapter(cfg, client)).run()
    store.close()

    # Simulating startup poll: replaying backfilled history must not reply.
    live_store = Store(cfg.db_path)
    fake = FakeClient()
    listener = Listener(cfg, live_store, fake)
    for msg_id, pk in (("m1", "100"),):
        listener.handle_message(
            SimpleNamespace(
                id=msg_id,
                user_id="uid-alice",
                username="alice",
                item_type="clip",
                timestamp=1_700_100_000,
                client_context="ctx",
                clip=SimpleNamespace(pk=pk, code="CODE"),
                media_share=None,
                xma_share=None,
                generic_xma=None,
            )
        )
    assert fake.sent == []

    # A genuinely NEW live re-share still triggers exactly one reply.
    live_msg = SimpleNamespace(
        id="m2",
        user_id="uid-bob",
        username="bob",
        item_type="clip",
        timestamp=1_700_500_000,
        client_context="ctx2",
        clip=SimpleNamespace(pk="100", code="CODE"),
        media_share=None,
        xma_share=None,
        generic_xma=None,
    )
    listener.handle_message(live_msg)

    assert len(fake.sent) == 1
    assert "@alice" in fake.sent[0].text
