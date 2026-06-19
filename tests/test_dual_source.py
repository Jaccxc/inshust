"""Dual-source orchestration: realtime (primary) + polling (safety net).

Card #5 acceptance, integration-tested fully OFFLINE with a fake instagrapi
client that emits scripted realtime events AND scripted poll pages through the
SAME ``Listener`` (one ``Store``):

- a message seen by BOTH realtime and polling yields exactly ONE reply;
- if realtime drops (delivers nothing), polling still delivers the call-out;
- ``run`` wires login -> realtime connect (primary) -> poll loop (safety net),
  all without real network or sleeping (finite loop via ``should_continue``).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from inshust.adapter import InstagramAdapter
from inshust.config import Config
from inshust.listener import Listener
from inshust.store import Store


def _config(tmp_path, **overrides):
    base = dict(
        thread_id="42",
        db_path=str(tmp_path / "db.sqlite"),
        timezone="Asia/Taipei",
        username="bot",
        password="pw",
        session_path=str(tmp_path / "session.json"),
        poll_interval_seconds=45,
    )
    base.update(overrides)
    return Config(**base)


class FakeRealtimeClient:
    """Records handlers; on ``connect`` delivers scripted events synchronously."""

    def __init__(self, scripted_events=None) -> None:
        self.handlers: dict[str, list] = {}
        self.scripted_events = list(scripted_events or [])

    def on(self, event, handler) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def connect(self) -> None:
        for event, payload in self.scripted_events:
            for handler in self.handlers.get(event, []):
                handler(payload)


def _rt(thread_id, item_id, user_id, clip_pk, ts):
    return {
        "message": {
            "thread_id": thread_id,
            "item_id": item_id,
            "user_id": user_id,
            "item_type": "clip",
            "timestamp": ts,
            "client_context": f"ctx-{item_id}",
            "clip": {"pk": clip_pk, "code": "CzAbc"},
        }
    }


def _poll_dm(item_id, user_id, clip_pk, ts):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    return SimpleNamespace(
        id=item_id,
        user_id=user_id,
        item_type="clip",
        timestamp=datetime.fromtimestamp(ts, tz=UTC),
        client_context=f"ctx-{item_id}",
        clip=SimpleNamespace(pk=clip_pk, code="CzAbc"),
        media_share=None,
        xma_share=None,
        generic_xma=None,
    )


def _thread(messages, users):
    from types import SimpleNamespace

    return SimpleNamespace(
        id="42",
        messages=list(messages),
        users=[SimpleNamespace(pk=pk, username=name) for pk, name in users.items()],
    )


def _build(cfg, *, realtime_events, poll_messages, poll_users):
    """Wire one adapter + listener + store over a single fake client."""
    client = MagicMock()
    realtime = FakeRealtimeClient(scripted_events=realtime_events)
    client.realtime_connect.side_effect = lambda *a, **k: realtime.connect()
    client.realtime_on.side_effect = realtime.on
    # instagrapi returns newest-first; mimic by reversing the chronological list.
    client.direct_thread.return_value = _thread(list(reversed(poll_messages)), poll_users)
    adapter = InstagramAdapter(cfg, client=client, sleeper=lambda s: None)
    store = Store(cfg.db_path)
    listener = Listener(cfg, store, adapter)
    return adapter, listener, client


def test_message_seen_by_both_realtime_and_polling_replies_once(tmp_path):
    cfg = _config(tmp_path)
    # alice shares reel 100 first (m1); bob re-shares it (m2). BOTH messages are
    # delivered via realtime AND via the poll page -> they overlap completely.
    realtime_events = [
        ("message", _rt("42", "m1", "uid-alice", "100", 1_700_000_000)),
        ("message", _rt("42", "m2", "uid-bob", "100", 1_700_100_000)),
    ]
    poll_messages = [
        _poll_dm("m1", "uid-alice", "100", 1_700_000_000),
        _poll_dm("m2", "uid-bob", "100", 1_700_100_000),
    ]
    adapter, listener, client = _build(
        cfg,
        realtime_events=realtime_events,
        poll_messages=poll_messages,
        poll_users={"uid-alice": "alice", "uid-bob": "bob"},
    )

    # Realtime fires first (primary), then a polling pass re-sees both.
    adapter.connect_realtime(listener)
    adapter.poll_once(listener)

    # Exactly ONE reply despite the message being seen by BOTH sources.
    assert client.direct_send.call_count == 1


def test_polling_still_delivers_when_realtime_drops(tmp_path):
    cfg = _config(tmp_path)
    # Realtime delivers NOTHING (connection dropped / never fired). The duplicate
    # must still be caught by the polling safety net.
    poll_messages = [
        _poll_dm("m1", "uid-alice", "100", 1_700_000_000),
        _poll_dm("m2", "uid-bob", "100", 1_700_100_000),
    ]
    adapter, listener, client = _build(
        cfg,
        realtime_events=[],  # realtime dropped: emits no events
        poll_messages=poll_messages,
        poll_users={"uid-alice": "alice", "uid-bob": "bob"},
    )

    adapter.connect_realtime(listener)  # connects but delivers nothing
    assert client.direct_send.call_count == 0  # realtime caught nothing

    adapter.poll_once(listener)  # safety net catches the duplicate

    assert client.direct_send.call_count == 1
    assert "@alice" in client.direct_send.call_args.args[0]


def test_run_connects_realtime_primary_then_runs_poll_safety_net(monkeypatch, tmp_path):
    """``run`` logs in once, connects realtime (primary), runs the poll loop.

    Driven offline: a stub adapter records the call order; the poll loop is
    finite via an injected ``should_continue``; no real client or sleeping.
    """
    import inshust.app as app

    monkeypatch.setattr(app, "load_config", lambda: _config(tmp_path))
    fake_adapter = MagicMock()
    monkeypatch.setattr(app, "InstagramAdapter", lambda config: fake_adapter)

    order: list[str] = []
    fake_adapter.login.side_effect = lambda: order.append("login")
    fake_adapter.connect_realtime.side_effect = lambda listener: order.append("connect_realtime")
    fake_adapter.poll_once.side_effect = lambda listener: order.append("poll_once")

    # Run a single finite poll cycle, then stop.
    cycles = {"n": 0}

    def should_continue() -> bool:
        cycles["n"] += 1
        return cycles["n"] <= 1

    app.run(should_continue=should_continue, sleeper=lambda s: None)

    # Login first, realtime connected (primary) BEFORE the poll loop starts.
    assert order[0] == "login"
    assert order[1] == "connect_realtime"
    assert "poll_once" in order[2:]
    fake_adapter.connect_realtime.assert_called_once()
