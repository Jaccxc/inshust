"""Behavior of the pure reply formatter.

The first-seen timestamp is epoch UTC; the reply renders it localized to the
configured timezone.
"""

from inshust.models import MEDIA_TYPE_CLIP, SeenRecord
from inshust.reply import format_reply


def _record(username="alice", first_sent_at=1_700_000_000):
    return SeenRecord(
        media_pk="100",
        shortcode="sc",
        media_type=MEDIA_TYPE_CLIP,
        first_sender_username=username,
        first_sender_user_id="uid",
        first_sent_at=first_sent_at,
        original_message_id="m1",
        original_client_context="ctx",
        thread_id="t-1",
    )


def test_reply_names_sender_and_localized_time_taipei():
    # 1700000000 == 2023-11-14 22:13:20 UTC == 2023-11-15 06:13 Asia/Taipei (+08).
    text = format_reply(_record(username="alice", first_sent_at=1_700_000_000), tz="Asia/Taipei")
    assert "@alice" in text
    assert "2023-11-15 06:13" in text


def test_reply_localizes_to_utc_when_configured():
    text = format_reply(_record(username="bob", first_sent_at=1_700_000_000), tz="UTC")
    assert "@bob" in text
    assert "2023-11-14 22:13" in text
