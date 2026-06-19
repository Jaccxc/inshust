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

    def resolve_media_pk_from_url(self, url: str) -> str:
        """Resolve a share ``url`` to its canonical media ``pk``.

        Used for url-only xma shares. The real instagrapi adapter (card #3)
        implements this via ``media_pk_from_url``; it must raise on an
        unresolvable url so the caller can skip that message gracefully. The
        media resolver caches the result so a repeated url needs no 2nd call.
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
    """In-memory ClientPort that records sent replies; no network.

    ``url_to_pk`` is the scripted resolution map for url-only xma shares;
    ``resolve_calls`` counts how many times the client was actually asked to
    resolve, so caching tests can assert a repeated url needs no 2nd call.
    """

    def __init__(self, url_to_pk: dict[str, str] | None = None) -> None:
        self.sent: list[SentReply] = []
        self._url_to_pk: dict[str, str] = dict(url_to_pk or {})
        self.resolve_calls = 0

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

    def resolve_media_pk_from_url(self, url: str) -> str:
        """Resolve ``url`` from the scripted map; raise if unknown."""
        self.resolve_calls += 1
        try:
            return self._url_to_pk[url]
        except KeyError as exc:
            raise KeyError(f"FakeClient cannot resolve url: {url}") from exc
