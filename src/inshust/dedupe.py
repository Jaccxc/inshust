"""The core dedupe decision.

``dedupe(store, event)`` is pure *given the injected store*: it makes no network
calls and holds no state of its own. It looks up the event's media pk; if the
media has been seen before it is a DUPLICATE (carrying the original record),
otherwise it records the share and reports FIRST_SEEN.

Message-id idempotency lives in the listener (the processed-messages gate), not
here, so this function stays a clean, heavily-testable decision.
"""

from __future__ import annotations

from inshust.models import DUPLICATE, FIRST_SEEN, DedupeDecision, ShareEvent
from inshust.store import Store


def dedupe(store: Store, event: ShareEvent) -> DedupeDecision:
    """Decide FIRST_SEEN vs DUPLICATE for ``event`` and record first-seens."""
    original = store.get_seen(event.media_key.pk)
    if original is not None:
        return DedupeDecision(kind=DUPLICATE, original=original)

    store.record_share(event)
    return DedupeDecision(kind=FIRST_SEEN, original=None)
