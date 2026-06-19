"""Behavior of configuration loading.

We drive ``load_config`` with an explicit environment mapping (so no real
process env or .env file is touched) and assert defaults + overrides.
"""

from inshust.config import Config, load_config


def test_defaults_when_only_thread_id_given():
    cfg = load_config(env={"IG_THREAD_ID": "thread-123"})
    assert isinstance(cfg, Config)
    assert cfg.thread_id == "thread-123"
    assert cfg.db_path == "./data/inshust.db"
    assert cfg.timezone == "Asia/Taipei"


def test_overrides_from_env():
    cfg = load_config(
        env={
            "IG_THREAD_ID": "t-9",
            "DB_PATH": "/data/inshust.db",
            "TZ": "UTC",
        }
    )
    assert cfg.thread_id == "t-9"
    assert cfg.db_path == "/data/inshust.db"
    assert cfg.timezone == "UTC"


def test_later_card_fields_present_but_optional():
    # Creds/session/proxy/poll-interval exist as a forward seam, unused now.
    cfg = load_config(
        env={
            "IG_THREAD_ID": "t-1",
            "IG_USERNAME": "burner",
            "IG_PROXY": "http://p:1",
            "POLL_INTERVAL_SECONDS": "30",
        }
    )
    assert cfg.username == "burner"
    assert cfg.proxy == "http://p:1"
    assert cfg.poll_interval_seconds == 30


def test_adapter_pacing_and_locale_fields_load_from_env():
    cfg = load_config(
        env={
            "IG_THREAD_ID": "t-1",
            "IG_LOCALE": "zh_TW",
            "ACTION_DELAY_MIN": "1.5",
            "ACTION_DELAY_MAX": "4.5",
            "POLL_AMOUNT": "50",
            "CHALLENGE_BACKOFF_SECONDS": "120",
        }
    )
    assert cfg.locale == "zh_TW"
    assert cfg.action_delay_min_seconds == 1.5
    assert cfg.action_delay_max_seconds == 4.5
    assert cfg.poll_amount == 50
    assert cfg.challenge_backoff_seconds == 120


def test_adapter_pacing_defaults():
    cfg = load_config(env={"IG_THREAD_ID": "t-1"})
    assert cfg.locale == "en_US"
    assert cfg.action_delay_min_seconds == 2.0
    assert cfg.action_delay_max_seconds == 6.0
    assert cfg.poll_amount == 20
