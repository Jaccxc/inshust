"""Minimal console wiring for the walking skeleton.

``build_listener`` assembles the pure+store+client pipeline from a Config and an
injected client. ``run`` is the console entry point: this slice loads config and
reports the bot is wired but does NOT log in or start a realtime/poll loop —
those arrive with the instagrapi adapter (card #3) and the listener loop
(cards #4/#5), which will plug a real ClientPort into ``build_listener`` and
drive ``Listener.handle_message``.
"""

from __future__ import annotations

from inshust.client import ClientPort
from inshust.config import Config, load_config
from inshust.listener import Listener
from inshust.store import Store


def build_listener(config: Config, client: ClientPort) -> Listener:
    """Assemble the per-message pipeline over a real SQLite store."""
    store = Store(config.db_path)
    return Listener(config, store, client)


def run() -> None:
    """Console entry point (no network in this slice)."""
    config = load_config()
    print(
        "inshust: walking skeleton wired "
        f"(thread_id={config.thread_id or '<unset>'}, db_path={config.db_path}, "
        f"timezone={config.timezone}). "
        "Live login + realtime/poll listener arrive in later cards."
    )
