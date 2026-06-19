"""Behavior tests for interactive challenge / 2FA login handling."""

from unittest.mock import MagicMock

from instagrapi.exceptions import TwoFactorRequired

from inshust.adapter import InstagramAdapter
from inshust.config import Config


def _cfg(tmp_path, **kw) -> Config:
    # session_path points at a nonexistent file so login() does a fresh login.
    return Config(
        thread_id="123",
        session_path=str(tmp_path / "no-session.json"),
        username="u",
        password="p",
        **kw,
    )


def test_challenge_code_handler_is_set_on_client_when_provided(tmp_path):
    client = MagicMock()

    def handler(username, choice):
        return "000000"

    adapter = InstagramAdapter(
        _cfg(tmp_path),
        client=client,
        sleeper=lambda s: None,
        challenge_code_handler=handler,
    )
    adapter.login()
    # The interactive handler is wired onto the instagrapi client so a login
    # checkpoint resolves by prompting instead of raising/pausing.
    assert client.challenge_code_handler is handler
    client.login.assert_called_once()


def test_no_challenge_handler_leaves_client_untouched(tmp_path):
    client = MagicMock(spec=["load_settings", "login", "dump_settings", "set_locale", "set_proxy"])
    adapter = InstagramAdapter(_cfg(tmp_path), client=client, sleeper=lambda s: None)
    adapter.login()
    assert not hasattr(client, "challenge_code_handler")


def test_two_factor_uses_interactive_provider_when_no_totp_seed(tmp_path):
    client = MagicMock()
    client.login.side_effect = [TwoFactorRequired("2fa needed"), True]
    provider = MagicMock(return_value="246810")
    adapter = InstagramAdapter(
        _cfg(tmp_path),
        client=client,
        sleeper=lambda s: None,
        two_factor_code_provider=provider,
    )
    adapter.login()
    provider.assert_called_once()
    assert client.login.call_count == 2
    assert client.login.call_args_list[1].kwargs["verification_code"] == "246810"


def test_two_factor_prefers_totp_seed_over_interactive_provider(tmp_path):
    client = MagicMock()
    client.login.side_effect = [TwoFactorRequired("2fa needed"), True]
    client.totp_generate_code.return_value = "135790"
    provider = MagicMock(return_value="000000")
    adapter = InstagramAdapter(
        _cfg(tmp_path, totp_seed="SEED123"),
        client=client,
        sleeper=lambda s: None,
        two_factor_code_provider=provider,
    )
    adapter.login()
    client.totp_generate_code.assert_called_once_with("SEED123")
    provider.assert_not_called()
    assert client.login.call_args_list[1].kwargs["verification_code"] == "135790"


def test_login_by_sessionid_used_when_configured(tmp_path):
    client = MagicMock()
    adapter = InstagramAdapter(
        _cfg(tmp_path, sessionid="SID-ABC"),
        client=client,
        sleeper=lambda s: None,
    )
    adapter.login()
    client.login_by_sessionid.assert_called_once_with("SID-ABC")
    client.login.assert_not_called()  # no password login / checkpoint flow
    client.dump_settings.assert_called_once()  # session persisted for reuse


def test_sessionid_takes_precedence_over_password(tmp_path):
    client = MagicMock()
    adapter = InstagramAdapter(
        _cfg(tmp_path, sessionid="SID-XYZ"),  # username/password also set by _cfg
        client=client,
        sleeper=lambda s: None,
    )
    adapter.login()
    client.login_by_sessionid.assert_called_once_with("SID-XYZ")
    client.login.assert_not_called()
