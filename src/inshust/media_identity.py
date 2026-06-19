"""Pure detection of shared reels/posts in a direct message.

``extract_share(message)`` is PURE: no I/O, no network. It inspects a
duck-typed message (only the attributes named below) and returns a
:class:`ShareRef` when the message carries a reel/post, else ``None``.

``clip`` and ``media_share`` both carry a full Media object exposing ``.pk`` and
``.code``, so their ShareRef is already canonical. The richer URL-only
``xma_share``/``generic_xma`` forms carry a share URL but no pk: those yield a
ShareRef with ``url`` set and ``pk``/``shortcode`` left ``None``; the media
resolver (card #4) turns that url into a pk so the same reel dedupes whether it
was shared as a clip or as an xma card.
"""

from __future__ import annotations

from dataclasses import dataclass

from inshust.models import MEDIA_TYPE_CLIP, MEDIA_TYPE_FEED, MEDIA_TYPE_XMA

# instagrapi item_type values for rich URL-only share cards.
_XMA_ITEM_TYPES = frozenset({"xma_share", "generic_xma", "xma_media_share"})


@dataclass(frozen=True)
class ShareRef:
    """A detected share before it is resolved to a canonical MediaKey.

    For ``clip``/``media_share`` the ``pk`` and ``shortcode`` are already
    present. The ``url`` field is the seam for xma forms (card #3): those will
    set ``url`` with ``pk``/``shortcode`` left ``None`` for the resolver.
    """

    pk: str | None
    shortcode: str | None
    media_type: str
    url: str | None = None


def _from_media(media: object, media_type: str) -> ShareRef | None:
    """Build a ShareRef from an embedded Media object, if it has a pk."""
    pk = getattr(media, "pk", None)
    if pk is None:
        return None
    code = getattr(media, "code", None)
    return ShareRef(
        pk=str(pk),
        shortcode=str(code) if code is not None else None,
        media_type=media_type,
    )


def _xma_url(message: object) -> str | None:
    """Extract the share URL from an xma message, or ``None`` if absent.

    Mirrors instagrapi: ``xma_share`` is a single ``MediaXma`` and
    ``generic_xma`` is a list of them; both expose the share URL on
    ``video_url``. We also accept a plain ``url`` attribute so the resolver
    seam stays easy to drive from fakes.
    """
    candidates = []
    xma_share = getattr(message, "xma_share", None)
    if xma_share is not None:
        candidates.append(xma_share)
    generic = getattr(message, "generic_xma", None)
    if generic:
        candidates.extend(generic)

    for xma in candidates:
        url = getattr(xma, "url", None) or getattr(xma, "video_url", None)
        if url:
            return str(url)
    return None


def extract_share(message: object) -> ShareRef | None:
    """Return a :class:`ShareRef` for reel/post shares, else ``None``.

    ``message`` is duck-typed: we only read ``item_type``, ``clip``,
    ``media_share`` and the xma fields (``xma_share``/``generic_xma``).
    """
    item_type = getattr(message, "item_type", None)

    if item_type == "clip":
        clip = getattr(message, "clip", None)
        if clip is not None:
            return _from_media(clip, MEDIA_TYPE_CLIP)

    if item_type == "media_share":
        media_share = getattr(message, "media_share", None)
        if media_share is not None:
            return _from_media(media_share, MEDIA_TYPE_FEED)

    if item_type in _XMA_ITEM_TYPES:
        url = _xma_url(message)
        if url is not None:
            # URL-only: pk/shortcode stay None until the resolver fills them.
            return ShareRef(pk=None, shortcode=None, media_type=MEDIA_TYPE_XMA, url=url)

    return None
