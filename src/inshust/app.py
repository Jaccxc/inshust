"""Console wiring + the dual-source run loop (realtime primary + poll net).

``build_listener`` assembles the pure+store+client pipeline from a Config and an
injected client. ``run_poll_loop`` is the dependable polling safety net: it logs
in via the adapter once, then repeatedly polls the configured thread and feeds
each message through the listener, spaced by ``config.poll_interval_seconds``.

``run`` is the console entry point: it loads config, builds the real
instagrapi-backed adapter + listener, logs in once, connects the MQTT realtime
channel as the PRIMARY low-latency source, then runs the polling loop as the
dependable safety net. Realtime delivers on instagrapi's own background thread
(via the handler registered in ``connect_realtime``); polling runs in the
foreground loop. Both feed the SAME listener / store, so the shared
processed-message-id gate guarantees a message seen by both yields exactly one
reply. The sleeper and loop-continuation are injectable so tests drive a finite
number of cycles with NO real network and NO real sleeping.

Where card #6 extends: the run-loop reconnect/backoff seam lives in
``run_poll_loop`` (the ``try/except OperatorPausedError`` cycle is the place to
add realtime-drop detection + ``connect_realtime`` retry/backoff). The send
retry/backoff seam lives in the adapter's single ``direct_send`` choke point
inside ``InstagramAdapter.send_reply``.
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
    login: bool = True,
) -> None:
    """Log in once (optional), then poll the thread each interval cycle.

    Each cycle fetches recent messages and feeds them through the listener's
    idempotent gate, so duplicates produce exactly one reply and re-polled
    messages are skipped. The loop stops cleanly if the adapter pauses on a
    challenge (operator action required). ``sleeper``/``should_continue`` are
    injected so tests run finite cycles without real sleeping or network.

    ``login`` is the seam for the dual-source ``run``: when realtime is connected
    first, ``run`` performs the single login itself and passes ``login=False``
    so the loop does not re-authenticate. This ``try/except OperatorPausedError``
    cycle is where card #6 adds realtime-drop detection + reconnect/backoff.
    """
    if login:
        adapter.login()
    while should_continue():
        try:
            adapter.poll_once(listener)
        except OperatorPausedError:
            logger.critical("bot paused (challenge/checkpoint) — stopping poll loop")
            return
        sleeper(config.poll_interval_seconds)


def run(
    *,
    sleeper: Callable[[float], None] = time.sleep,
    should_continue: Callable[[], bool] = lambda: True,
) -> None:
    """Console entry point: realtime (primary) + polling (safety net) together.

    Logs in once, connects the MQTT realtime channel as the primary low-latency
    source (it delivers on instagrapi's own background thread via the handler
    registered in ``connect_realtime``), then runs the polling loop as the
    dependable safety net in the foreground. Both feed the SAME listener/store,
    so the shared processed-message-id gate yields exactly one reply per message
    even when realtime and polling overlap. If realtime drops, polling still
    delivers. ``sleeper``/``should_continue`` are injected so tests drive finite
    cycles with no real network or sleeping.
    """
    logging.basicConfig(level=logging.INFO)
    config = load_config()
    adapter = InstagramAdapter(config)
    listener = build_listener(config, adapter)
    logger.info(
        "inshust starting: thread_id=%s db_path=%s timezone=%s poll=%ss (realtime primary)",
        config.thread_id or "<unset>",
        config.db_path,
        config.timezone,
        config.poll_interval_seconds,
    )
    adapter.login()
    try:
        adapter.connect_realtime(listener)
    except OperatorPausedError:
        logger.critical("bot paused (challenge/checkpoint) during realtime connect — stopping")
        return
    except Exception:
        # Realtime is best-effort/experimental: a connect failure must NOT stop
        # the bot — polling is the dependable safety net. Card #6 adds reconnect.
        logger.warning("realtime connect failed; relying on polling safety net", exc_info=True)
    run_poll_loop(
        adapter,
        listener,
        config=config,
        sleeper=sleeper,
        should_continue=should_continue,
        login=False,
    )
