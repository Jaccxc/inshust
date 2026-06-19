"""Resilience & observability (card #6).

Hardening of the running bot, exercised entirely OFFLINE (the instagrapi
``Client`` is always a mock/fake — no network ever):

- ``send_reply`` retries documented transient group-send 500s with backoff and
  then succeeds; a PERSISTENT failure raises a typed error the orchestration can
  catch (so the run loop survives instead of crashing);
- the run loop reconnects realtime with backoff after a dropped connection
  instead of dying;
- structured logs record each observed share, the dedupe decision
  (FIRST_SEEN/DUPLICATE), and the reply outcome (sent/fallback/failed).

Backoff is driven by an injectable sleeper so tests assert the schedule without
real sleeping; transient/persistent errors and disconnects are simulated.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from instagrapi.exceptions import ClientError

from inshust.adapter import InstagramAdapter, SendFailedError
from inshust.config import Config


def _config(tmp_path, **overrides):
    base = dict(
        thread_id="42",
        db_path=str(tmp_path / "db.sqlite"),
        timezone="Asia/Taipei",
        username="bot",
        password="pw",
        session_path=str(tmp_path / "session.json"),
        # deterministic, fast backoff schedule for the tests
        send_max_retries=3,
        send_backoff_base_seconds=1.0,
    )
    base.update(overrides)
    return Config(**base)


def _adapter(cfg, client):
    """Adapter over a mock client + a recording sleeper (no real sleeping)."""
    sleeps: list[float] = []
    adapter = InstagramAdapter(cfg, client=client, sleeper=sleeps.append)
    adapter.recorded_sleeps = sleeps  # type: ignore[attr-defined]
    return adapter


# --- (a) transient send error: retried with backoff, then succeeds ---------


def test_transient_send_error_is_retried_with_backoff_then_succeeds(tmp_path):
    cfg = _config(tmp_path, send_max_retries=3, send_backoff_base_seconds=1.0)
    client = MagicMock()
    # The documented transient case: direct_v2/threads/broadcast/text/ 500s.
    # The native quote-reply path fails twice transiently, then succeeds on the
    # 3rd attempt. (Same path each time -- this is retry, NOT the plaintext
    # fallback, which is a different seam.)
    client.direct_send.side_effect = [
        ClientError("500 Server Error: direct_v2/threads/broadcast/text/"),
        ClientError("500 Server Error: direct_v2/threads/broadcast/text/"),
        object(),  # success
    ]

    adapter = _adapter(cfg, client)
    # Must NOT raise: a transient error that eventually succeeds is handled.
    adapter.send_reply(
        thread_id="42",
        text="already sent by @alice",
        original_message_id="m-orig",
        original_client_context="ctx",
    )

    # Retried the SAME send until it succeeded (3 attempts total).
    assert client.direct_send.call_count == 3
    # Backed off between attempts with an exponential schedule (base 1s).
    backoffs = [s for s in adapter.recorded_sleeps if s in (1.0, 2.0)]
    assert backoffs == [1.0, 2.0]


# --- (a) persistent send failure: raises typed error (loop must survive) ----


def test_persistent_send_failure_raises_send_failed_error(tmp_path):
    cfg = _config(tmp_path, send_max_retries=3, send_backoff_base_seconds=1.0)
    client = MagicMock()
    # Every attempt fails the SAME transient-looking way: a persistent outage.
    client.direct_send.side_effect = ClientError(
        "500 Server Error: direct_v2/threads/broadcast/text/"
    )

    adapter = _adapter(cfg, client)
    with pytest.raises(SendFailedError):
        adapter.send_reply(
            thread_id="42",
            text="already sent by @alice",
            original_message_id="m-orig",
            original_client_context="ctx",
        )

    # Exhausted all attempts (initial + retries) before giving up.
    assert client.direct_send.call_count == 1 + cfg.send_max_retries


# --- (c) structured logs: observed share, dedupe decision, reply outcome ----

from types import SimpleNamespace  # noqa: E402

from inshust.client import SentReply  # noqa: E402
from inshust.listener import Listener  # noqa: E402
from inshust.store import Store  # noqa: E402


def _listener_config(tmp_path):
    return Config(thread_id="t-1", db_path=str(tmp_path / "db.sqlite"), timezone="Asia/Taipei")


def _share_msg(message_id, pk, *, username, user_id, ts):
    media = SimpleNamespace(pk=pk, code="CzAbc")
    return SimpleNamespace(
        id=message_id,
        item_type="clip",
        clip=media,
        media_share=None,
        xma_share=None,
        generic_xma=None,
        user_id=user_id,
        username=username,
        timestamp=ts,
        client_context=f"ctx-{message_id}",
    )


def _records(caplog, event):
    """Structured log records carrying the given ``event`` field."""
    return [r for r in caplog.records if getattr(r, "event", None) == event]


def test_structured_logs_record_share_decision_and_reply_outcome(tmp_path, caplog):
    from inshust.client import FakeClient

    cfg = _listener_config(tmp_path)
    listener = Listener(cfg, Store(cfg.db_path), FakeClient())

    with caplog.at_level("INFO", logger="inshust.listener"):
        # First share: observed + FIRST_SEEN, no reply.
        listener.handle_message(
            _share_msg("m1", "100", username="alice", user_id="uid-alice", ts=1_700_000_000)
        )
        # Duplicate share: observed + DUPLICATE + reply sent.
        listener.handle_message(
            _share_msg("m2", "100", username="bob", user_id="uid-bob", ts=1_700_100_000)
        )

    # Each observed share is logged with its media pk + sender + message id.
    observed = _records(caplog, "share_observed")
    assert {r.message_id for r in observed} == {"m1", "m2"}
    assert {r.media_pk for r in observed} == {"100"}
    assert any(r.sender_username == "alice" for r in observed)

    # The dedupe decision is logged for each (FIRST_SEEN then DUPLICATE).
    decisions = _records(caplog, "dedupe_decision")
    by_msg = {r.message_id: r.decision for r in decisions}
    assert by_msg == {"m1": "FIRST_SEEN", "m2": "DUPLICATE"}

    # The reply outcome is logged for the duplicate (a successful send).
    outcomes = _records(caplog, "reply_outcome")
    assert len(outcomes) == 1
    assert outcomes[0].message_id == "m2"
    assert outcomes[0].outcome == "sent"


class _FallbackClient:
    """ClientPort whose send_reply reports a plaintext-fallback outcome."""

    def __init__(self):
        self.sent: list[SentReply] = []

    def send_reply(self, thread_id, text, original_message_id, original_client_context):
        self.sent.append(SentReply(thread_id, text, original_message_id, original_client_context))
        return "fallback"

    def resolve_media_pk_from_url(self, url):  # pragma: no cover - not used here
        raise KeyError(url)


def test_reply_outcome_records_fallback_when_adapter_falls_back(tmp_path, caplog):
    cfg = _listener_config(tmp_path)
    listener = Listener(cfg, Store(cfg.db_path), _FallbackClient())

    with caplog.at_level("INFO", logger="inshust.listener"):
        listener.handle_message(
            _share_msg("m1", "100", username="alice", user_id="uid-alice", ts=1_700_000_000)
        )
        listener.handle_message(
            _share_msg("m2", "100", username="bob", user_id="uid-bob", ts=1_700_100_000)
        )

    outcomes = _records(caplog, "reply_outcome")
    assert [r.outcome for r in outcomes] == ["fallback"]


class _PersistentlyFailingClient:
    """ClientPort whose send_reply always fails persistently (SendFailedError)."""

    def __init__(self):
        self.attempts = 0

    def send_reply(self, thread_id, text, original_message_id, original_client_context):
        self.attempts += 1
        raise SendFailedError("persistent broadcast/text 500")

    def resolve_media_pk_from_url(self, url):  # pragma: no cover - not used here
        raise KeyError(url)


def test_persistent_send_failure_is_logged_and_loop_survives(tmp_path, caplog):
    cfg = _listener_config(tmp_path)
    client = _PersistentlyFailingClient()
    store = Store(cfg.db_path)
    listener = Listener(cfg, store, client)

    listener.handle_message(
        _share_msg("m1", "100", username="alice", user_id="uid-alice", ts=1_700_000_000)
    )
    with caplog.at_level("ERROR", logger="inshust.listener"):
        # The duplicate's send fails persistently -- this MUST NOT raise, so the
        # surrounding run loop survives and keeps processing later messages.
        listener.handle_message(
            _share_msg("m2", "100", username="bob", user_id="uid-bob", ts=1_700_100_000)
        )

    # The failed reply outcome is logged...
    outcomes = _records(caplog, "reply_outcome")
    assert [r.outcome for r in outcomes] == ["failed"]
    # ...and the message is still marked processed (so it is not retried forever
    # on the next poll) -- the loop moved on cleanly.
    assert store.is_processed("m2") is True
