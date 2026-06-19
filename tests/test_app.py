"""Behavior of the minimal console wiring.

This slice has no realtime/poll loop yet (cards #4/#5). ``build_listener``
assembles the pipeline from a Config + FakeClient so the seams are exercised
without any network; ``main`` runs without raising.
"""

from unittest.mock import MagicMock

from inshust.app import build_listener, run_poll_loop
from inshust.client import FakeClient
from inshust.config import Config
from inshust.listener import Listener


def test_build_listener_assembles_the_pipeline(tmp_path):
    cfg = Config(thread_id="t-1", db_path=str(tmp_path / "db.sqlite"))
    listener = build_listener(cfg, FakeClient())
    assert isinstance(listener, Listener)


def test_main_runs_the_poll_loop_without_touching_the_network(monkeypatch, tmp_path):
    """``main`` wires config -> adapter -> poll loop. No real client/network.

    We stub the adapter and the loop so the entry point is exercised end-to-end
    without constructing a real instagrapi client or sleeping.
    """
    import inshust
    import inshust.app as app

    assert callable(inshust.main)

    monkeypatch.setattr(
        app, "load_config", lambda: Config(thread_id="42", db_path=str(tmp_path / "db.sqlite"))
    )
    fake_adapter = MagicMock()
    monkeypatch.setattr(app, "InstagramAdapter", lambda config: fake_adapter)

    calls = {}

    def fake_loop(adapter, listener, *, config, **kwargs):
        calls["adapter"] = adapter
        calls["listener"] = listener

    monkeypatch.setattr(app, "run_poll_loop", fake_loop)

    inshust.main()

    # The real instagrapi adapter (our stub) is handed to the poll loop.
    assert calls["adapter"] is fake_adapter
    assert isinstance(calls["listener"], Listener)


def test_run_poll_loop_logs_in_then_polls_each_interval(tmp_path):
    """The poll loop logs in once, polls each cycle, sleeps the interval.

    Driven by an injected ``should_continue`` so the test runs a finite number
    of cycles with NO real network and NO real sleeping.
    """
    cfg = Config(thread_id="42", db_path=str(tmp_path / "db.sqlite"), poll_interval_seconds=45)
    adapter = MagicMock()
    listener = object()
    sleeps: list[float] = []
    cycles = {"n": 0}

    def should_continue() -> bool:
        cycles["n"] += 1
        return cycles["n"] <= 2  # run exactly 2 poll cycles, then stop

    run_poll_loop(
        adapter,
        listener,
        config=cfg,
        sleeper=sleeps.append,
        should_continue=should_continue,
    )

    # Logged in exactly once, before polling.
    adapter.login.assert_called_once()
    # Polled the thread once per cycle, feeding the listener.
    assert adapter.poll_once.call_count == 2
    adapter.poll_once.assert_called_with(listener)
    # Slept the configured interval between cycles.
    assert sleeps == [45, 45]
