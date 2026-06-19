"""Group-thread discovery helper (card #8) — a SEPARATE console entry point.

The operator needs the group's thread id to configure ``IG_THREAD_ID`` (PRD user
story 21), but Instagram identifies group threads only by an opaque numeric id.
This is a small, manually-run command (``inshust-threads``) that logs in via the
adapter (reusing a persisted session when present) and lists the account's Direct
GROUP threads — id, title, and member usernames — in an easy-to-copy format.

It is NOT the live bot: it reads the inbox once and prints; it never replies,
polls, or backfills. The instagrapi client is isolated behind
``InstagramAdapter.list_group_threads``, so this whole helper is tested offline
with a mocked client / mocked adapter (no network).
"""

from __future__ import annotations

import logging

from inshust.adapter import InstagramAdapter
from inshust.config import Config, load_config
from inshust.logging_setup import (
    console_challenge_code_handler,
    console_two_factor_code_provider,
    setup_logging,
)

logger = logging.getLogger(__name__)


def run(*, config: Config, adapter: InstagramAdapter) -> None:
    """Log in via the adapter, then print the account's group threads.

    Prints each group thread on its own block: the thread id first (so it is
    trivial to copy into ``IG_THREAD_ID``), then its title and the member
    usernames so the operator can recognize which group it is. When the account
    is in no group threads, says so plainly rather than printing nothing.

    ``config``/``adapter`` are injected so tests drive this with a mocked adapter
    and assert the printed output, with no real login and no network.
    """
    adapter.login()
    groups = adapter.list_group_threads()

    if not groups:
        print("No group threads found for this account.")
        return

    print(f"Found {len(groups)} group thread(s). Copy a thread id into IG_THREAD_ID:")
    print()
    for group in groups:
        members = ", ".join(group.members) if group.members else "(no members listed)"
        title = group.title or "(untitled)"
        print(f"  IG_THREAD_ID={group.id}")
        print(f"    title:   {title}")
        print(f"    members: {members}")
        print()


def main() -> None:  # pragma: no cover - thin console wiring, exercised via run()
    """Console entry point ``inshust-threads``: list the account's group threads.

    Loads config, builds the real instagrapi-backed adapter, and runs the
    discovery print. Run this by hand to find the group's thread id, then set
    ``IG_THREAD_ID`` in your ``.env`` before starting the bot.
    """
    config = load_config()
    setup_logging(config.log_level)
    adapter = InstagramAdapter(
        config,
        challenge_code_handler=console_challenge_code_handler,
        two_factor_code_provider=console_two_factor_code_provider,
    )
    run(config=config, adapter=adapter)
