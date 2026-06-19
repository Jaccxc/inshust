"""Console wiring + the runnable polling loop.

``build_listener`` assembles the pure+store+client pipeline from a Config and an
injected client. ``run_poll_loop`` is the dependable polling safety net: it logs
in via the adapter once, then repeatedly polls the configured thread and feeds
each message through the listener, spaced by ``config.poll_interval_seconds``.

``run`` is the console entry point: it loads config, builds the real
instagrapi-backed adapter + listener, and starts the poll loop. The sleeper and
loop-continuation are injectable so tests drive a finite number of cycles with
NO real network and NO real sleeping (realtime is card #4; retry/backoff is #5).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from inshust.adapter import InstagramAdapter, OperatorPausedError
from inshust.client import ClientPort
from inshust.config import Config, load_config
from inshust.listener import Listener
from inshust.store import Store

logger = logging.getLogger(__name__)


def build_listener(config: Config, client: ClientPort) -> Listener:
    """Assemble the per-message pipeline over a real SQLite store."""
    store = Store(config.db_path)
    return Listener(config, store, client)


def run_poll_loop(
    adapter: InstagramAdapter,
    listener: Listener,
    *,
    config: Config,
    sleeper: Callable[[float], None] = time.sleep,
    should_continue: Callable[[], bool] = lambda: True,
) -> None:
    """Log in once, then poll the thread each ``poll_interval_seconds`` cycle.

    Each cycle fetches recent messages and feeds them through the listener's
    idempotent gate, so duplicates produce exactly one reply and re-polled
    messages are skipped. The loop stops cleanly if the adapter pauses on a
    challenge (operator action required). ``sleeper``/``should_continue`` are
    injected so tests run finite cycles without real sleeping or network.
    """
    adapter.login()
    while should_continue():
        try:
            adapter.poll_once(listener)
        except OperatorPausedError:
            logger.critical("bot paused (challenge/checkpoint) — stopping poll loop")
            return
        sleeper(config.poll_interval_seconds)


def run() -> None:
    """Console entry point: build the adapter + listener and poll the thread."""
    logging.basicConfig(level=logging.INFO)
    config = load_config()
    adapter = InstagramAdapter(config)
    listener = build_listener(config, adapter)
    logger.info(
        "inshust starting: thread_id=%s db_path=%s timezone=%s poll=%ss",
        config.thread_id or "<unset>",
        config.db_path,
        config.timezone,
        config.poll_interval_seconds,
    )
    run_poll_loop(adapter, listener, config=config)
