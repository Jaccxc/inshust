"""Pure demerit (警告 / 小過 / 大過) scoring + display conversion.

The stored unit is the **警告** (warning). Conversion is fixed:
``3 警告 = 1 小過``, ``3 小過 = 1 大過`` (so ``1 小過 = 3 警告``,
``1 大過 = 9 警告``). A repeated share earns a penalty sized by how *stale* the
reel was — the gap between the original first-share and this re-share. The
longer the gap, the heavier the penalty:

==========================  ==========  ==========
gap (re-share lateness)     penalty     警告 units
==========================  ==========  ==========
<= 1 day                    警告一支     1
<= 3 days                   警告兩支     2
<= 1 week                   小過一支     3
<= 2 weeks                  大過一支     9
<= 3 months                 大過三支     27
otherwise                   大過五支     45
==========================  ==========  ==========

No I/O — everything here is a pure function of the gap / the warning total.
"""

from __future__ import annotations

from dataclasses import dataclass

WARNINGS_PER_MINOR = 3  # 警告 -> 小過
MINOR_PER_MAJOR = 3  # 小過 -> 大過
WARNINGS_PER_MAJOR = WARNINGS_PER_MINOR * MINOR_PER_MAJOR  # 9

_DAY = 86_400


@dataclass(frozen=True)
class Penalty:
    """A penalty earned for one repeated share: warning units + its label."""

    warnings: int
    label: str  # e.g. "警告一支", "小過一支", "大過五支"


# (inclusive max gap in seconds, penalty) — first matching tier wins.
_TIERS: tuple[tuple[int, Penalty], ...] = (
    (1 * _DAY, Penalty(1, "警告一支")),
    (3 * _DAY, Penalty(2, "警告兩支")),
    (7 * _DAY, Penalty(WARNINGS_PER_MINOR, "小過一支")),  # 3
    (14 * _DAY, Penalty(WARNINGS_PER_MAJOR, "大過一支")),  # 9
    (90 * _DAY, Penalty(WARNINGS_PER_MAJOR * 3, "大過三支")),  # 27
)
_OVER = Penalty(WARNINGS_PER_MAJOR * 5, "大過五支")  # 45 (gap > 3 months)


def classify(gap_seconds: int) -> Penalty:
    """Return the :class:`Penalty` for a re-share that was ``gap_seconds`` late."""
    gap = max(0, int(gap_seconds))
    for max_gap, penalty in _TIERS:
        if gap <= max_gap:
            return penalty
    return _OVER


def breakdown(total_warnings: int) -> str:
    """Render a warning total as ``大過 / 小過 / 警告`` standing (display only)."""
    total = max(0, int(total_warnings))
    if total == 0:
        return "0 警告"
    major, rem = divmod(total, WARNINGS_PER_MAJOR)
    minor, warn = divmod(rem, WARNINGS_PER_MINOR)
    parts: list[str] = []
    if major:
        parts.append(f"{major} 大過")
    if minor:
        parts.append(f"{minor} 小過")
    if warn:
        parts.append(f"{warn} 警告")
    return "、".join(parts) + f"（共 {total} 警告）"
