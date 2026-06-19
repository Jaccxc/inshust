"""Pure reply-text formatter.

Given the original first-seen record and a timezone name, render the call-out
text. The stored ``first_sent_at`` is epoch seconds (UTC); we localize it to the
operator's timezone (Asia/Taipei by default) so the time is meaningful to the
group. No I/O.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from inshust.models import SeenRecord

_TIME_FORMAT = "%Y-%m-%d %H:%M"


def _format_lateness(seconds: int) -> str:
    """Render a non-negative duration in Traditional Chinese (days/hours/minutes)."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} 秒"

    parts: list[str] = []
    days, rem = divmod(seconds, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes, _ = divmod(rem, 60)
    if days:
        parts.append(f"{days} 天")
    if hours:
        parts.append(f"{hours} 小時")
    if minutes or not parts:
        parts.append(f"{minutes} 分鐘")
    return " ".join(parts)


def format_reply(
    original: SeenRecord,
    *,
    duplicate_username: str,
    duplicate_sent_at: int,
    tz: str,
) -> str:
    """Return e.g. ``"@alice 於 … 已分享過，@bob 慢了 1 小時"``."""
    when = datetime.fromtimestamp(original.first_sent_at, ZoneInfo(tz))
    lateness = _format_lateness(duplicate_sent_at - original.first_sent_at)
    return (
        f"@{original.first_sender_username} 於 {when.strftime(_TIME_FORMAT)} 已分享過，"
        f"@{duplicate_username} 慢了 {lateness}"
    )
