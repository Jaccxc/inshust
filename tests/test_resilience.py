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
