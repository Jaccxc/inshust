"""Behavior of the real instagrapi-backed adapter (card #3).

Every test here mocks ``instagrapi.Client`` — the adapter NEVER logs in for
real or touches the network. We assert observable behavior through the
adapter's public interface: session reuse vs fresh login, TOTP supplied on 2FA,
challenge -> paused + alert + backoff (no loop), proxy/locale/device applied,
human-like delay invoked, native quote-reply with plaintext fallback, url->pk
delegation, and polling that feeds Listener.handle_message end-to-end.
"""

from __future__ import annotations

from datetime import UTC
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


def test_corrupt_session_falls_back_to_fresh_login(tmp_path):
    cfg = _config(tmp_path)
    (tmp_path / "session.json").write_text("not-json")
    client = MagicMock()
    # Loading a corrupt session blows up -> the adapter recovers via fresh login.
    client.load_settings.side_effect = ValueError("corrupt session")
    client.login.return_value = True

    _adapter(cfg, client).login()

    client.load_settings.assert_called_once()
    client.login.assert_called_once_with("bot", "pw")
    client.dump_settings.assert_called_once_with(cfg.session_path)


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


# --- proxy / locale applied from config ------------------------------------


def test_proxy_and_locale_applied_when_configured(tmp_path):
    cfg = _config(
        tmp_path,
        proxy="http://user:pass@1.2.3.4:8080",
        locale="zh_TW",
    )
    (tmp_path / "session.json").write_text("{}")  # reuse path: still applies proxy/locale
    client = MagicMock()

    _adapter(cfg, client).login()

    client.set_proxy.assert_called_once_with("http://user:pass@1.2.3.4:8080")
    client.set_locale.assert_called_once_with("zh_TW")


def test_no_proxy_call_when_proxy_unset(tmp_path):
    cfg = _config(tmp_path, proxy=None)
    (tmp_path / "session.json").write_text("{}")
    client = MagicMock()

    _adapter(cfg, client).login()

    # No proxy configured -> the adapter must not touch the proxy setting.
    client.set_proxy.assert_not_called()
    # Locale always has a default, so it is still applied.
    client.set_locale.assert_called_once_with(cfg.locale)


# --- send_reply: native quote-reply, plaintext fallback, delay -------------


def test_send_reply_uses_native_quote_reply(tmp_path):
    cfg = _config(tmp_path)
    client = MagicMock()
    adapter = _adapter(cfg, client)

    adapter.send_reply(
        thread_id="42",
        text="already sent by @alice on 2026-06-10 14:32",
        original_message_id="m-orig",
        original_client_context="ctx-orig",
    )

    client.direct_send.assert_called_once()
    call = client.direct_send.call_args
    # The text is sent into the configured thread...
    assert call.args[0] == "already sent by @alice on 2026-06-10 14:32"
    assert call.kwargs["thread_ids"] == [42]
    # ...as a native quote-reply that references the ORIGINAL message.
    reply_to = call.kwargs["reply_to_message"]
    assert reply_to is not None
    assert getattr(reply_to, "id", None) == "m-orig"
    assert getattr(reply_to, "client_context", None) == "ctx-orig"


def test_send_reply_invokes_human_like_delay(tmp_path):
    cfg = _config(tmp_path, action_delay_min_seconds=3.0, action_delay_max_seconds=3.0)
    client = MagicMock()
    adapter = _adapter(cfg, client)

    adapter.send_reply(
        thread_id="42",
        text="hi",
        original_message_id="m1",
        original_client_context=None,
    )

    # A randomized human-like delay within [min, max] was requested before send.
    assert adapter.recorded_sleeps
    assert all(3.0 <= s <= 3.0 for s in adapter.recorded_sleeps)


def test_send_reply_falls_back_to_plain_send_when_quote_reply_fails(tmp_path):
    cfg = _config(tmp_path)
    client = MagicMock()
    # First (native quote-reply) call rejected; plain send accepted.
    client.direct_send.side_effect = [Exception("reply_to_message not accepted"), SimpleNamespace()]
    adapter = _adapter(cfg, client)

    adapter.send_reply(
        thread_id="42",
        text="already sent by @alice",
        original_message_id="m-orig",
        original_client_context="ctx",
    )

    assert client.direct_send.call_count == 2
    # The fallback still delivers the text into the thread, with NO reply_to.
    fallback = client.direct_send.call_args_list[1]
    assert fallback.args[0] == "already sent by @alice"
    assert fallback.kwargs["thread_ids"] == [42]
    assert "reply_to_message" not in fallback.kwargs


# --- resolve_media_pk_from_url: delegate + delay ---------------------------


def test_resolve_media_pk_delegates_to_client(tmp_path):
    cfg = _config(tmp_path)
    client = MagicMock()
    client.media_pk_from_url.return_value = 123456789
    adapter = _adapter(cfg, client)

    pk = adapter.resolve_media_pk_from_url("https://www.instagram.com/reel/CzAbc/")

    client.media_pk_from_url.assert_called_once_with("https://www.instagram.com/reel/CzAbc/")
    assert pk == "123456789"  # normalized to str for the canonical MediaKey
    assert adapter.recorded_sleeps  # spaced by a human-like delay too


# --- normalization: instagrapi DirectMessage -> what extract_share wants ---


def test_normalize_converts_timestamp_to_epoch_and_resolves_username():
    from datetime import datetime

    from inshust.adapter import normalize_direct_message

    media = SimpleNamespace(pk=100, code="CzAbc")
    dm = SimpleNamespace(
        id="m1",
        user_id="uid-alice",
        item_type="clip",
        timestamp=datetime(2026, 6, 10, 14, 32, tzinfo=UTC),
        client_context="ctx",
        clip=media,
        media_share=None,
        xma_share=None,
        generic_xma=None,
    )

    norm = normalize_direct_message(dm, users_by_id={"uid-alice": "alice"})

    assert norm.id == "m1"
    assert norm.user_id == "uid-alice"
    assert norm.username == "alice"  # resolved from the thread's user map
    assert norm.item_type == "clip"
    # datetime -> epoch seconds (int) as the pipeline expects.
    assert norm.timestamp == int(datetime(2026, 6, 10, 14, 32, tzinfo=UTC).timestamp())
    assert norm.client_context == "ctx"
    # share fields pass through so extract_share sees the real media object.
    assert norm.clip is media


def test_normalize_unknown_user_falls_back_to_user_id():
    from datetime import datetime

    from inshust.adapter import normalize_direct_message

    dm = SimpleNamespace(
        id="m2",
        user_id="uid-x",
        item_type="text",
        timestamp=datetime(2026, 6, 10, tzinfo=UTC),
        client_context=None,
        clip=None,
        media_share=None,
        xma_share=None,
        generic_xma=None,
    )

    norm = normalize_direct_message(dm, users_by_id={})

    # Unknown sender -> username falls back to the user id (never crashes).
    assert norm.username == "uid-x"


# --- polling: fetch recent thread messages -> feed handle_message ----------


def _dm(message_id, *, user_id, item_type, ts, clip_pk=None, ctx="ctx"):
    from datetime import datetime

    media = SimpleNamespace(pk=clip_pk, code="CzAbc") if clip_pk is not None else None
    return SimpleNamespace(
        id=message_id,
        user_id=user_id,
        item_type=item_type,
        timestamp=datetime.fromtimestamp(ts, tz=UTC),
        client_context=ctx,
        clip=media if item_type == "clip" else None,
        media_share=None,
        xma_share=None,
        generic_xma=None,
    )


def _thread(messages, users):
    # instagrapi returns messages newest-first; mimic that ordering.
    return SimpleNamespace(
        id="42",
        messages=list(messages),
        users=[SimpleNamespace(pk=pk, username=name) for pk, name in users.items()],
    )


def test_poll_once_feeds_messages_and_produces_dedupe_reply(tmp_path):
    from inshust.listener import Listener
    from inshust.store import Store

    cfg = _config(tmp_path)
    client = MagicMock()
    # Two shares of the same reel (pk 100): alice first, then bob -> one reply.
    # instagrapi yields newest-first, so bob's (later) message is first.
    msgs = [
        _dm("m2", user_id="uid-bob", item_type="clip", ts=1_700_100_000, clip_pk="100"),
        _dm("m1", user_id="uid-alice", item_type="clip", ts=1_700_000_000, clip_pk="100"),
    ]
    client.direct_thread.return_value = _thread(
        msgs, users={"uid-alice": "alice", "uid-bob": "bob"}
    )

    adapter = _adapter(cfg, client)
    store = Store(cfg.db_path)
    listener = Listener(cfg, store, adapter)

    adapter.poll_once(listener)

    client.direct_thread.assert_called_once()
    # The thread id is fetched as an int.
    assert client.direct_thread.call_args.args[0] == 42
    # Exactly one duplicate reply, naming alice (the first sender), quoting m2.
    assert client.direct_send.call_count == 1
    sent_text = client.direct_send.call_args.args[0]
    assert "@alice" in sent_text


def test_poll_once_refuses_when_paused(tmp_path):
    from inshust.listener import Listener
    from inshust.store import Store

    cfg = _config(tmp_path)
    client = MagicMock()
    client.login.side_effect = ChallengeRequired("checkpoint")
    adapter = _adapter(cfg, client)
    # A challenge during login pauses the bot (observable via .paused).
    with pytest.raises(OperatorPausedError):
        adapter.login()
    assert adapter.paused is True

    listener = Listener(cfg, Store(cfg.db_path), adapter)
    with pytest.raises(OperatorPausedError):
        adapter.poll_once(listener)

    # A paused bot must not poll the thread.
    client.direct_thread.assert_not_called()
