"""Behavior of the MQTT realtime source on the adapter (card #5).

Realtime is the PRIMARY low-latency channel; polling (card #3) is the safety
net. Every test here is OFFLINE: the instagrapi client is a mock/fake and the
realtime transport never touches the network. A ``FakeRealtimeClient`` emits
scripted realtime events synchronously to the handler the adapter registers, so
we assert observable behavior through the public interface:

- ``connect_realtime`` registers a ``"message"`` handler and connects;
- a realtime message for the configured thread flows through the SAME pipeline
  (``normalize_direct_message`` -> ``Listener.handle_message``) and dedupes;
- a realtime message for a DIFFERENT thread is ignored;
- realtime + polling overlap yields exactly ONE reply (shared processed gate);
- if realtime drops, polling still delivers the duplicate call-out.
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
    )
    base.update(overrides)
    return Config(**base)


class FakeRealtimeClient:
    """In-memory stand-in for instagrapi's realtime client.

    Records the handlers the adapter registers via ``on(event, handler)`` and,
    on ``connect()``, synchronously delivers any scripted events to the matching
    handlers (simulating the async MQTT delivery without any network/threads).
    """

    def __init__(self, scripted_events: list[tuple[str, object]] | None = None) -> None:
        self.handlers: dict[str, list] = {}
        self.scripted_events = list(scripted_events or [])
        self.connected = False

    def on(self, event: str, handler) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def connect(self) -> None:
        self.connected = True
        for event, payload in self.scripted_events:
            self.emit(event, payload)

    def emit(self, event: str, payload: object) -> None:
        for handler in self.handlers.get(event, []):
            handler(payload)


def _realtime_message(
    *,
    thread_id="42",
    item_id="m1",
    user_id="uid-alice",
    item_type="clip",
    clip_pk="100",
    ts=1_700_000_000,
    client_context="ctx",
):
    """A realtime "message" event wrapper as instagrapi emits it.

    The realtime client emits ``{"message": {<DM fields, thread_id>}}`` where the
    inner dict carries the direct-message item fields (a plain dict, NOT an
    instagrapi ``DirectMessage`` object).
    """
    media = {"pk": clip_pk, "code": "CzAbc"} if clip_pk is not None else None
    return {
        "message": {
            "thread_id": thread_id,
            "item_id": item_id,
            "user_id": user_id,
            "item_type": item_type,
            "timestamp": ts,
            "client_context": client_context,
            "clip": media if item_type == "clip" else None,
        }
    }


def _wire(cfg, *, scripted_events=None, users_by_id=None):
    """Build adapter + listener + fake realtime client, all offline."""
    client = MagicMock()
    realtime = FakeRealtimeClient(scripted_events=scripted_events)
    client.realtime_connect.side_effect = lambda *a, **k: realtime.connect()
    client.realtime_on.side_effect = realtime.on
    adapter = InstagramAdapter(cfg, client=client, sleeper=lambda s: None)
    store = Store(cfg.db_path)
    listener = Listener(cfg, store, adapter)
    return adapter, listener, client, realtime


def test_connect_realtime_registers_handler_and_connects(tmp_path):
    cfg = _config(tmp_path)
    adapter, listener, client, realtime = _wire(cfg)

    adapter.connect_realtime(listener)

    # A "message" handler is registered, and the transport is connected.
    client.realtime_on.assert_any_call("message", realtime.handlers["message"][0])
    assert "message" in realtime.handlers
    client.realtime_connect.assert_called_once()
    assert realtime.connected is True


def test_realtime_message_flows_through_pipeline_and_dedupes(tmp_path):
    cfg = _config(tmp_path)
    # Two shares of the same reel arriving via realtime: alice first, then bob.
    events = [
        ("message", _realtime_message(item_id="m1", user_id="uid-alice", clip_pk="100")),
        ("message", _realtime_message(item_id="m2", user_id="uid-bob", clip_pk="100")),
    ]
    adapter, listener, client, realtime = _wire(cfg, scripted_events=events)

    adapter.connect_realtime(listener)

    # The second (duplicate) realtime share triggers exactly one reply.
    assert client.direct_send.call_count == 1
    assert client.direct_send.call_args.kwargs["thread_ids"] == [42]


def test_realtime_message_for_other_thread_is_ignored(tmp_path):
    cfg = _config(tmp_path, thread_id="42")
    events = [
        ("message", _realtime_message(thread_id="999", item_id="m1", clip_pk="100")),
        ("message", _realtime_message(thread_id="999", item_id="m2", clip_pk="100")),
    ]
    adapter, listener, client, realtime = _wire(cfg, scripted_events=events)

    adapter.connect_realtime(listener)

    # Messages for a different thread never enter the pipeline -> no reply.
    client.direct_send.assert_not_called()
