"""Pure detection of shared reels/posts in a direct message.

``extract_share(message)`` is PURE: no I/O, no network. It inspects a
duck-typed message (only the attributes named below) and returns a
:class:`ShareRef` when the message carries a reel/post, else ``None``.

This slice handles the two simplest forms, ``clip`` and ``media_share``, which
both carry a full Media object exposing ``.pk`` and ``.code``. Richer
URL-only ``xma_share``/``generic_xma`` forms are intentionally out of scope
here; card #3 adds them at the seam marked below by returning a ShareRef that
carries a ``url`` instead of a ``pk`` (the resolver then turns it into a pk).
"""

from __future__ import annotations

from dataclasses import dataclass

from inshust.models import MEDIA_TYPE_CLIP, MEDIA_TYPE_FEED


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


def extract_share(message: object) -> ShareRef | None:
    """Return a :class:`ShareRef` for reel/post shares, else ``None``.

    ``message`` is duck-typed: we only read ``item_type``, ``clip`` and
    ``media_share``.
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

    # --- xma seam (card #3) ----------------------------------------------
    # item_type in {"xma_share", "generic_xma", "xma_media_share"} carries a
    # URL only. Detect it here and return a ShareRef(url=..., pk=None); the
    # media resolver will turn the URL into a pk. Not handled in this slice.

    return None
