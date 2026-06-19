"""Behavior of pure media-identity detection (``extract_share``).

We feed duck-typed fake messages (only the attributes the function reads) and
assert the produced ShareRef, or None for non-media. No instagrapi objects.
"""

from types import SimpleNamespace

from inshust.media_identity import extract_share
from inshust.models import MEDIA_TYPE_CLIP, MEDIA_TYPE_FEED, MEDIA_TYPE_XMA


def _media(pk, code):
    return SimpleNamespace(pk=pk, code=code)


def _msg(**kw):
    # Defaults: every share-form attribute absent unless set by the test.
    base = dict(item_type="text", clip=None, media_share=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_clip_share_yields_ref_with_pk_shortcode_and_clip_type():
    msg = _msg(item_type="clip", clip=_media(pk=999, code="CzAbc"))
    ref = extract_share(msg)
    assert ref is not None
    assert ref.pk == "999"  # normalized to str
    assert ref.shortcode == "CzAbc"
    assert ref.media_type == MEDIA_TYPE_CLIP


def test_media_share_yields_ref_with_feed_type():
    msg = _msg(item_type="media_share", media_share=_media(pk=555, code="CmXyz"))
    ref = extract_share(msg)
    assert ref is not None
    assert ref.pk == "555"
    assert ref.shortcode == "CmXyz"
    assert ref.media_type == MEDIA_TYPE_FEED


def test_text_message_is_not_a_share():
    assert extract_share(_msg(item_type="text")) is None


def test_link_message_is_not_a_share():
    assert extract_share(_msg(item_type="link")) is None


def _xma(video_url):
    # Mirrors instagrapi.MediaXma: the share URL lives on ``video_url``.
    return SimpleNamespace(video_url=video_url)


def test_xma_share_yields_url_only_ref_with_xma_type():
    msg = _msg(item_type="xma_share", xma_share=_xma("https://www.instagram.com/reel/CzAbc/"))
    ref = extract_share(msg)
    assert ref is not None
    assert ref.url == "https://www.instagram.com/reel/CzAbc/"
    assert ref.pk is None  # url-only: the resolver turns it into a pk
    assert ref.shortcode is None
    assert ref.media_type == MEDIA_TYPE_XMA


def test_generic_xma_list_share_yields_url_only_ref():
    msg = _msg(
        item_type="generic_xma",
        generic_xma=[_xma("https://www.instagram.com/reel/CmXyz/")],
    )
    ref = extract_share(msg)
    assert ref is not None
    assert ref.url == "https://www.instagram.com/reel/CmXyz/"
    assert ref.pk is None
    assert ref.media_type == MEDIA_TYPE_XMA


def test_xma_media_share_item_type_is_detected():
    msg = _msg(item_type="xma_media_share", xma_share=_xma("https://instagr.am/p/Cabc/"))
    ref = extract_share(msg)
    assert ref is not None
    assert ref.url == "https://instagr.am/p/Cabc/"
    assert ref.media_type == MEDIA_TYPE_XMA


def test_xma_clip_item_type_is_detected():
    msg = _msg(item_type="xma_clip", xma_share=_xma("https://www.instagram.com/reel/CzClip/"))
    ref = extract_share(msg)
    assert ref is not None
    assert ref.url == "https://www.instagram.com/reel/CzClip/"
    assert ref.media_type == MEDIA_TYPE_XMA


def test_xma_clip_with_embedded_clip_uses_pk():
    msg = _msg(item_type="xma_clip", clip=_media(pk=777, code="CzClip"), xma_share=None)
    ref = extract_share(msg)
    assert ref is not None
    assert ref.pk == "777"
    assert ref.media_type == MEDIA_TYPE_CLIP


def test_xma_item_type_but_no_url_is_not_a_share():
    # An xma item without any extractable URL is treated as non-media.
    msg = _msg(item_type="xma_share", xma_share=None, generic_xma=None)
    assert extract_share(msg) is None
