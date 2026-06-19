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

Card #6 resilience lives here: ``run_poll_loop`` survives a dropped connection
(a transient ``ClientError`` from ``poll_once``) by backing off with capped
exponential backoff and reconnecting realtime instead of crashing; a clean poll
resets the backoff. The send retry/backoff + persistent-failure handling lives
in the adapter (``send_reply``/``_send_with_retry``) and the listener
(structured ``share_observed``/``dedupe_decision``/``reply_outcome`` logs;
catching ``SendFailedError`` so the loop keeps going).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from inshust.adapter import InstagramAdapter, OperatorPausedError, is_transient_error
from inshust.client import ClientPort
from inshust.config import Config, load_config
from inshust.listener import Listener
from inshust.logging_setup import setup_logging
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
    so the loop does not re-authenticate.

    Card #6 resilience: a dropped connection (a transient ``ClientError`` —
    connection drop / timeout / 5xx — from ``poll_once``) does NOT crash the
    loop. The loop logs it, backs off with exponential backoff (base
    ``send_backoff_base_seconds``, capped at ``reconnect_max_backoff_seconds``),
    RECONNECTS the realtime channel, and continues. A clean poll resets the
    backoff so a later drop starts low again. A genuine challenge/checkpoint
    (``OperatorPausedError``) still stops the loop — that needs a human.
    """
    if login:
        adapter.login()
    reconnect_attempt = 0
    while should_continue():
        try:
            adapter.poll_once(listener)
            reconnect_attempt = 0  # a clean poll resets the reconnect backoff
        except OperatorPausedError:
            logger.critical("bot paused (challenge/checkpoint) — stopping poll loop")
            return
        except Exception as exc:
            if not is_transient_error(exc):
                raise
            backoff = min(
                config.send_backoff_base_seconds * (2**reconnect_attempt),
                config.reconnect_max_backoff_seconds,
            )
            logger.warning(
                "connection dropped (attempt %d); backing off %.1fs and reconnecting: %s",
                reconnect_attempt + 1,
                backoff,
                exc,
            )
            sleeper(backoff)
            _reconnect_realtime(adapter, listener)
            reconnect_attempt += 1
            continue
        sleeper(config.poll_interval_seconds)


def _reconnect_realtime(adapter: InstagramAdapter, listener: Listener) -> None:
    """Re-establish the realtime channel after a drop (best-effort).

    Realtime is the experimental low-latency PRIMARY; polling is the dependable
    safety net. A reconnect failure must NOT stop the bot — polling keeps
    catching duplicates — so a failed reconnect is logged and swallowed.
    """
    try:
        adapter.connect_realtime(listener)
    except OperatorPausedError:
        raise
    except Exception:
        logger.warning("realtime reconnect failed; relying on polling safety net", exc_info=True)


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
    config = load_config()
    setup_logging(config.log_level)
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
        # the bot — polling is the dependable safety net, and ``run_poll_loop``
        # reconnects realtime on the next transient drop (card #6).
        logger.warning("realtime connect failed; relying on polling safety net", exc_info=True)
    run_poll_loop(
        adapter,
        listener,
        config=config,
        sleeper=sleeper,
        should_continue=should_continue,
        login=False,
    )
