"""Behavior of pure media-identity detection (``extract_share``).

We feed duck-typed fake messages (only the attributes the function reads) and
assert the produced ShareRef, or None for non-media. No instagrapi objects.
"""

from types import SimpleNamespace

from inshust.media_identity import extract_share
from inshust.models import MEDIA_TYPE_CLIP, MEDIA_TYPE_FEED


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
