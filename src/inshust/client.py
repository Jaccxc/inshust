"""The Instagram client seam.

``ClientPort`` is the thin I/O boundary the listener depends on. The real
instagrapi-backed adapter (cards #3/#4 — login, session persistence, native
quote-reply with plaintext fallback, retry/backoff) implements this same port,
so the orchestration never imports instagrapi directly and stays testable.

``FakeClient`` is the in-memory double used in every test: it records the
replies it was asked to send instead of touching the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class ClientPort(Protocol):
    """The send-side of the Instagram client the listener depends on."""

    def send_reply(
        self,
        thread_id: str,
        text: str,
        original_message_id: str,
        original_client_context: str | None,
    ) -> None:
        """Send ``text`` into ``thread_id``, quoting the original share.

        The real adapter uses native quote-reply (``reply_to_message``) keyed by
        ``original_message_id``/``original_client_context`` when possible, with a
        plaintext fallback. This slice only needs the call to be recordable.
        """
        ...


@dataclass(frozen=True)
class SentReply:
    """A reply the FakeClient was asked to send (for test assertions)."""

    thread_id: str
    text: str
    original_message_id: str
    original_client_context: str | None


class FakeClient:
    """In-memory ClientPort that records sent replies; no network."""

    def __init__(self) -> None:
        self.sent: list[SentReply] = []

    def send_reply(
        self,
        thread_id: str,
        text: str,
        original_message_id: str,
        original_client_context: str | None,
    ) -> None:
        self.sent.append(
            SentReply(
                thread_id=thread_id,
                text=text,
                original_message_id=original_message_id,
                original_client_context=original_client_context,
            )
        )
