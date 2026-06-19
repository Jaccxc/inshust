"""Behavior of the pure reply formatter.

The first-seen timestamp is epoch UTC; the reply renders it localized to the
configured timezone.
"""

from inshust.models import MEDIA_TYPE_CLIP, SeenRecord
from inshust.reply import _format_lateness, format_reply


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
    text = format_reply(
        _record(username="alice", first_sent_at=1_700_000_000),
        duplicate_username="bob",
        duplicate_sent_at=1_700_003_600,
        tz="Asia/Taipei",
    )
    assert text == "@alice 於 2023-11-15 06:13 已分享過，@bob 慢了 1 小時"


def test_reply_localizes_to_utc_when_configured():
    text = format_reply(
        _record(username="bob", first_sent_at=1_700_000_000),
        duplicate_username="carol",
        duplicate_sent_at=1_700_086_400,
        tz="UTC",
    )
    assert text == "@bob 於 2023-11-14 22:13 已分享過，@carol 慢了 1 天"


def test_format_lateness_covers_days_hours_minutes_and_seconds():
    assert _format_lateness(45) == "45 秒"
    assert _format_lateness(3_600) == "1 小時"
    assert _format_lateness(90_000) == "1 天 1 小時"
    assert _format_lateness(100_000) == "1 天 3 小時 46 分鐘"
