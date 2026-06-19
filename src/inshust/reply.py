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


def format_reply(original: SeenRecord, tz: str) -> str:
    """Return e.g. ``"already sent by @alice on 2023-11-15 06:13"``."""
    when = datetime.fromtimestamp(original.first_sent_at, ZoneInfo(tz))
    return f"already sent by @{original.first_sender_username} on {when.strftime(_TIME_FORMAT)}"
