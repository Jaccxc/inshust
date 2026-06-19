"""Behavior of the minimal console wiring.

This slice has no realtime/poll loop yet (cards #4/#5). ``build_listener``
assembles the pipeline from a Config + FakeClient so the seams are exercised
without any network; ``main`` runs without raising.
"""

from inshust.app import build_listener
from inshust.client import FakeClient
from inshust.config import Config
from inshust.listener import Listener


def test_build_listener_assembles_the_pipeline(tmp_path):
    cfg = Config(thread_id="t-1", db_path=str(tmp_path / "db.sqlite"))
    listener = build_listener(cfg, FakeClient())
    assert isinstance(listener, Listener)


def test_main_is_callable_and_runs_without_network():
    import inshust

    assert callable(inshust.main)
    # Should not raise or hit the network (no login in this slice).
    inshust.main()
