"""Behavior of the real instagrapi-backed adapter (card #3).

Every test here mocks ``instagrapi.Client`` — the adapter NEVER logs in for
real or touches the network. We assert observable behavior through the
adapter's public interface: session reuse vs fresh login, TOTP supplied on 2FA,
challenge -> paused + alert + backoff (no loop), proxy/locale/device applied,
human-like delay invoked, native quote-reply with plaintext fallback, url->pk
delegation, and polling that feeds Listener.handle_message end-to-end.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from instagrapi.exceptions import ChallengeRequired, TwoFactorRequired

from inshust.adapter import InstagramAdapter, OperatorPausedError
from inshust.config import Config


def _config(tmp_path, **overrides):
    base = dict(
        thread_id="42",
        db_path=str(tmp_path / "db.sqlite"),
        timezone="Asia/Taipei",
        username="bot",
        password="pw",
        totp_seed="SEED123",
        session_path=str(tmp_path / "session.json"),
    )
    base.update(overrides)
    return Config(**base)


def _adapter(cfg, client, sleeper=None):
    """Build an adapter over a pre-built mock client + a recording sleeper."""
    sleeps: list[float] = []

    def _sleep(seconds):
        sleeps.append(seconds)

    adapter = InstagramAdapter(cfg, client=client, sleeper=sleeper or _sleep)
    adapter.recorded_sleeps = sleeps  # type: ignore[attr-defined]
    return adapter


# --- session reuse vs fresh login ------------------------------------------


def test_existing_session_is_reused_no_fresh_login(tmp_path):
    cfg = _config(tmp_path)
    # A session file already exists on disk -> the adapter must reuse it.
    (tmp_path / "session.json").write_text("{}")
    client = MagicMock()

    _adapter(cfg, client).login()

    client.load_settings.assert_called_once()
    client.login.assert_not_called()


def test_fresh_login_when_no_session_then_persists(tmp_path):
    cfg = _config(tmp_path)
    # No session file on disk -> a real credential login must happen.
    client = MagicMock()
    client.login.return_value = True

    _adapter(cfg, client).login()

    client.login.assert_called_once_with("bot", "pw")
    # Session is persisted so the next start reuses it.
    client.dump_settings.assert_called_once_with(cfg.session_path)
    client.load_settings.assert_not_called()


# --- 2FA (TOTP) ------------------------------------------------------------


def test_totp_code_supplied_when_2fa_required(tmp_path):
    cfg = _config(tmp_path)
    client = MagicMock()
    # First login attempt demands 2FA; the retry with a code succeeds.
    client.login.side_effect = [TwoFactorRequired("2fa needed"), True]
    client.totp_generate_code.return_value = "654321"

    _adapter(cfg, client).login()

    # TOTP generated from the configured seed...
    client.totp_generate_code.assert_called_once_with("SEED123")
    # ...and the retry supplies that exact code.
    assert client.login.call_count == 2
    retry = client.login.call_args_list[1]
    assert retry.kwargs.get("verification_code") == "654321"
    # Successful 2FA login still persists the session.
    client.dump_settings.assert_called_once_with(cfg.session_path)


# --- challenge / checkpoint: pause + alert + back off, do NOT loop ---------


def test_challenge_pauses_alerts_and_backs_off_without_looping(tmp_path, caplog):
    cfg = _config(tmp_path)
    client = MagicMock()
    client.login.side_effect = ChallengeRequired("checkpoint")

    adapter = _adapter(cfg, client)
    with pytest.raises(OperatorPausedError):
        with caplog.at_level("CRITICAL"):
            adapter.login()

    # Paused state surfaced for the operator.
    assert adapter.paused is True
    # A prominent alert was logged.
    assert any(r.levelname == "CRITICAL" for r in caplog.records)
    # It backed off once (slept) and did NOT loop the login.
    assert client.login.call_count == 1
    assert adapter.recorded_sleeps  # a backoff delay was requested
    # No session is persisted from a failed challenge login.
    client.dump_settings.assert_not_called()


def test_paused_adapter_refuses_to_act_further(tmp_path):
    cfg = _config(tmp_path)
    client = MagicMock()
    client.login.side_effect = ChallengeRequired("checkpoint")
    adapter = _adapter(cfg, client)
    with pytest.raises(OperatorPausedError):
        adapter.login()

    # Once paused, the bot must not keep sending into the thread.
    with pytest.raises(OperatorPausedError):
        adapter.send_reply(
            thread_id="42",
            text="x",
            original_message_id="m1",
            original_client_context=None,
        )
    client.direct_send.assert_not_called()
