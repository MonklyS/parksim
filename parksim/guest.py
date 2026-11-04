"""Guests: needs, preferences and mood."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .grid import Pos

# Rates per hour. Thirst also scales with heat.
HUNGER_PER_HOUR = 0.12
THIRST_PER_HOUR = 0.15
BLADDER_PER_HOUR = 0.16
ENERGY_PER_HOUR = 0.06
URGENT_NEED = 0.85  # above this, an unmet need starts to sour the guest's mood
UNMET_NEED_MOOD_PER_HOUR = 6.0

NEEDS = ("hunger", "thirst", "bladder", "energy")


class GuestState(StrEnum):
    DECIDING = "deciding"
    WALKING = "walking"
    QUEUED = "queued"
    RIDING = "riding"
    AT_SHOW = "at_show"
    SERVICE = "service"
    RESTING = "resting"
    LEAVING = "leaving"
    GONE = "gone"


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return min(high, max(low, value))


@dataclass(eq=False, slots=True, kw_only=True)
class Guest:
    id: int
    arrived_at: float
    leave_by: float
    pos: Pos
    thrill: float  # 0 = avoids intense rides, 1 = thrill seeker
    patience_minutes: float  # wait they will accept for a ride they want
    budget: float
    walk_factor: float = 1.0
    happiness: float = 75.0
    hunger: float = 0.2
    thirst: float = 0.2
    bladder: float = 0.1
    energy: float = 1.0
    updated_at: float = 0.0

    state: GuestState = GuestState.DECIDING
    target: str | None = None
    token: int = 0  # bumps whenever a pending wake-up should be ignored
    joined_at: float = 0.0
    posted_seen: int | None = None
    last_wait: float = 0.0
    plan_showtime: float | None = None
    pending_care: bool = False  # became unwell/injured; head to First Aid next
    needs_care: bool = False  # currently heading to / being treated at First Aid
    leave_reason: str = ""

    # Current walk, so a viewer can draw the guest moving along the path.
    walk_from: Pos = (0, 0)
    walk_start: float = 0.0
    walk_end: float = 0.0
    route: list[Pos] | None = None

    ridden: dict[str, int] = field(default_factory=dict)
    rides: int = 0
    spent: float = 0.0

    @property
    def money_left(self) -> float:
        return self.budget - self.spent

    def mood(self, delta: float) -> None:
        if delta > 0:  # good moments matter less the happier a guest already is
            delta *= (100.0 - self.happiness) / 50.0
        self.happiness = _clamp(self.happiness + delta, 0.0, 100.0)

    def advance(self, now: float, heat: float = 1.0) -> None:
        """Bring needs up to date for the time elapsed since the last update."""
        hours = (now - self.updated_at) / 3600
        if hours <= 0:
            return
        self.hunger = _clamp(self.hunger + HUNGER_PER_HOUR * hours)
        self.thirst = _clamp(self.thirst + THIRST_PER_HOUR * hours * heat)
        self.bladder = _clamp(self.bladder + BLADDER_PER_HOUR * hours)
        self.energy = _clamp(self.energy - ENERGY_PER_HOUR * hours)
        unmet = sum(1 for v in (self.hunger, self.thirst, self.bladder) if v >= URGENT_NEED)
        self.mood(-UNMET_NEED_MOOD_PER_HOUR * unmet * hours)
        self.updated_at = now

    def apply(self, effects: dict[str, float]) -> None:
        for need, delta in effects.items():
            if need in NEEDS:
                setattr(self, need, _clamp(getattr(self, need) + delta))
