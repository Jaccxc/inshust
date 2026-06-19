"""Turn a :class:`ShareRef` into a canonical :class:`MediaKey`.

The :class:`MediaResolver` is the I/O seam between pure detection (media
identity) and the pure dedupe decision. ``clip``/``media_share`` refs already
carry a ``pk``, so they resolve with no client call. URL-only xma refs are
resolved via the injected client's ``resolve_media_pk_from_url`` and the
``url -> pk`` result is cached in the store, so the same reel shared as a clip
and as an xma card collapses to the same dedupe key and a repeated url never
needs a second client call.

The client is injected (``ClientPort``) so this is fully testable with a fake
and a real temp SQLite store — no network. Resolution failures are surfaced as
``None`` (logged) rather than raised, so the listener can skip that one message
and keep running.
"""

from __future__ import annotations

import logging
import time

from inshust.client import ClientPort
from inshust.media_identity import ShareRef
from inshust.models import MediaKey
from inshust.store import Store

logger = logging.getLogger(__name__)


class MediaResolver:
    """Resolve a detected :class:`ShareRef` to a canonical :class:`MediaKey`."""

    def __init__(self, client: ClientPort, store: Store) -> None:
        self._client = client
        self._store = store

    def resolve(self, ref: ShareRef) -> MediaKey | None:
        """Return a :class:`MediaKey` for ``ref``, or ``None`` if unresolvable.

        - If ``ref.pk`` is present, build the key directly (no client call).
        - Otherwise resolve ``ref.url`` via the store cache, then the client,
          caching new resolutions. A missing url, or a client failure, yields
          ``None`` (logged) so the caller can skip the message gracefully.
        """
        if ref.pk is not None:
            return MediaKey(pk=ref.pk, shortcode=ref.shortcode, media_type=ref.media_type)

        if ref.url is None:
            logger.warning("share ref has neither pk nor url; cannot resolve: %r", ref)
            return None

        pk = self._resolve_url(ref.url)
        if pk is None:
            return None
        return MediaKey(pk=pk, shortcode=ref.shortcode, media_type=ref.media_type)

    def _resolve_url(self, url: str) -> str | None:
        """Resolve ``url`` to a pk via the cache first, then the client."""
        cached = self._store.get_cached_pk(url)
        if cached is not None:
            return cached

        try:
            pk = self._client.resolve_media_pk_from_url(url)
        except Exception:
            logger.exception("failed to resolve media pk from url: %s", url)
            return None

        if not pk:
            logger.warning("client returned no pk for url: %s", url)
            return None

        self._store.cache_url_pk(url, str(pk), resolved_at=int(time.time()))
        return str(pk)
