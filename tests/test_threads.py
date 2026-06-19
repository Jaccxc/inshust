"""Behavior of the group-thread discovery helper (card #8).

The operator needs to find the group's thread id to configure ``IG_THREAD_ID``
(PRD user story 21). The adapter's ``list_group_threads`` lists the account's
Direct *group* threads (id, title, member usernames) over instagrapi's
``direct_threads()``, filtering ``is_group``; the ``inshust.threads`` helper logs
in via the adapter and prints them in an easy-to-copy format.

Every test mocks ``instagrapi.Client`` — no real login, no network. We assert
observable behavior through the public interface: which threads are returned (and
that 1:1/non-group threads are filtered out), the id/title/members carried, and
the printed output (thread id present and easy to copy, title + member usernames
shown).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from inshust.adapter import InstagramAdapter
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


def _adapter(cfg, client):
    """Build an adapter over a pre-built mock client + a no-op sleeper."""
    return InstagramAdapter(cfg, client=client, sleeper=lambda _s: None)


def _thread(thread_id, *, title, is_group, usernames):
    """A DirectThread-shaped mock: id, thread_title, is_group, users[]."""
    return SimpleNamespace(
        id=thread_id,
        thread_title=title,
        is_group=is_group,
        users=[SimpleNamespace(pk=str(i), username=name) for i, name in enumerate(usernames)],
    )


# --- adapter.list_group_threads -------------------------------------------


def test_list_group_threads_returns_id_title_members_for_group_threads(tmp_path):
    cfg = _config(tmp_path)
    client = MagicMock()
    client.direct_threads.return_value = [
        _thread("111", title="Reel Crew", is_group=True, usernames=["alice", "bob", "carol"]),
        _thread("222", title="Movie Night", is_group=True, usernames=["dave", "erin"]),
    ]

    groups = _adapter(cfg, client).list_group_threads()

    assert len(groups) == 2
    first, second = groups
    assert first.id == "111"
    assert first.title == "Reel Crew"
    assert first.members == ["alice", "bob", "carol"]
    assert second.id == "222"
    assert second.title == "Movie Night"
    assert second.members == ["dave", "erin"]


def test_list_group_threads_filters_out_non_group_threads(tmp_path):
    cfg = _config(tmp_path)
    client = MagicMock()
    client.direct_threads.return_value = [
        _thread("111", title="Reel Crew", is_group=True, usernames=["alice", "bob"]),
        # A 1:1 DM is NOT a group thread -> must be filtered out.
        _thread("999", title=None, is_group=False, usernames=["alice"]),
    ]

    groups = _adapter(cfg, client).list_group_threads()

    assert [g.id for g in groups] == ["111"]


def test_list_group_threads_paused_returns_nothing(tmp_path):
    """A paused bot (challenge/checkpoint) must not call the network."""
    from instagrapi.exceptions import ChallengeRequired

    from inshust.adapter import OperatorPausedError

    cfg = _config(tmp_path)
    client = MagicMock()
    client.login.side_effect = ChallengeRequired("checkpoint")
    adapter = _adapter(cfg, client)
    import pytest

    with pytest.raises(OperatorPausedError):
        adapter.login()

    with pytest.raises(OperatorPausedError):
        adapter.list_group_threads()
    client.direct_threads.assert_not_called()


# --- inshust.threads helper: log in + print easy-to-copy output ------------


def test_threads_main_logs_in_then_prints_id_title_members(tmp_path, capsys):
    from inshust import threads

    cfg = _config(tmp_path)
    adapter = MagicMock()
    adapter.list_group_threads.return_value = [
        SimpleNamespace(id="111", title="Reel Crew", members=["alice", "bob"]),
        SimpleNamespace(id="222", title="Movie Night", members=["carol"]),
    ]

    threads.run(config=cfg, adapter=adapter)

    # It must log in via the adapter before listing.
    adapter.login.assert_called_once()
    adapter.list_group_threads.assert_called_once()

    out = capsys.readouterr().out
    # Each thread id is present and easy to copy verbatim.
    assert "111" in out
    assert "222" in out
    # Titles and member usernames are shown for the operator to recognize them.
    assert "Reel Crew" in out
    assert "Movie Night" in out
    assert "alice" in out
    assert "bob" in out
    assert "carol" in out


def test_threads_main_reports_when_no_group_threads(tmp_path, capsys):
    from inshust import threads

    cfg = _config(tmp_path)
    adapter = MagicMock()
    adapter.list_group_threads.return_value = []

    threads.run(config=cfg, adapter=adapter)

    out = capsys.readouterr().out
    assert "no group threads" in out.lower()
