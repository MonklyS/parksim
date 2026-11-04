"""Placed buildings: rides, shows and service facilities (food, restrooms, shops, first aid)."""

from __future__ import annotations

import math
from collections import Counter, deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from .catalog import META_KEYS, TEMPLATES
from .errors import ParkError
from .grid import Pos
from .timeutil import parse_clock

if TYPE_CHECKING:
    from .guest import Guest


class Category(StrEnum):
    RIDE = "ride"
    SHOW = "show"
    FOOD = "food"
    RESTROOM = "restroom"
    SHOP = "shop"
    FIRST_AID = "first_aid"


class Closure(StrEnum):
    """Reasons an attraction can be closed. Several can apply at once (e.g. broken *and*
    on lightning hold); it reopens only when every reason has cleared."""

    BREAKDOWN = "breakdown"
    INSPECTION = "inspection"
    WEATHER = "weather"
    MANUAL = "manual"


CLOSURE_PRIORITY = (Closure.BREAKDOWN, Closure.INSPECTION, Closure.WEATHER, Closure.MANUAL)


@dataclass
class AttractionStats:
    guests_served: int = 0
    wait_total: float = 0.0
    wait_max: float = 0.0
    waits_recorded: int = 0
    posted_error_total: float = 0.0  # sum of (posted - actual) wait, minutes
    posted_samples: int = 0
    peak_posted: int = 0
    dispatches: int = 0
    performances: int = 0
    shows_cancelled: int = 0
    revenue: float = 0.0
    downtime: float = 0.0  # seconds closed during operating hours
    closed_since: float | None = None
    closures: Counter[str] = field(default_factory=Counter)

    def record_wait(self, seconds: float) -> None:
        self.wait_total += seconds
        self.waits_recorded += 1
        self.wait_max = max(self.wait_max, seconds)

    @property
    def avg_wait_minutes(self) -> float:
        return self.wait_total / self.waits_recorded / 60 if self.waits_recorded else 0.0


@dataclass(eq=False, kw_only=True)
class Attraction:
    id: str
    name: str
    template: str
    category: Category
    x: int
    y: int
    w: int
    h: int
    entrance: Pos
    indoor: bool = False
    appeal: float = 5.0
    closures: set[Closure] = field(default_factory=set)
    stats: AttractionStats = field(default_factory=AttractionStats)
    spec: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_open(self) -> bool:
        return not self.closures

    @property
    def status(self) -> str:
        for reason in CLOSURE_PRIORITY:
            if reason in self.closures:
                return reason.value
        return "open"

    def footprint(self) -> list[Pos]:
        return [
            (x, y) for x in range(self.x, self.x + self.w) for y in range(self.y, self.y + self.h)
        ]


@dataclass(eq=False, kw_only=True)
class Ride(Attraction):
    capacity: int
    cycle_seconds: float
    ride_seconds: float
    intensity: float = 5.0
    mtbf_hours: float = 0.0
    mttr_minutes: float = 30.0
    queue: deque[Guest] = field(default_factory=deque, repr=False)
    loop_token: int = 0

    @property
    def throughput_per_hour(self) -> float:
        """Theoretical hourly ride capacity (THRC): riders per dispatch x dispatches per hour."""
        return self.capacity * 3600 / self.cycle_seconds

    def estimated_wait_minutes(self) -> float:
        """Wait for someone joining the back of the line right now (Little's law, discretised
        to whole dispatches)."""
        return math.ceil(len(self.queue) / self.capacity) * self.cycle_seconds / 60


@dataclass(eq=False, kw_only=True)
class Show(Attraction):
    capacity: int
    duration_minutes: float
    showtimes: list[float]
    lookahead_minutes: float = 25.0
    audience: dict[float, list[Guest]] = field(default_factory=dict, repr=False)
    live_showtime: float | None = None

    @property
    def duration_seconds(self) -> float:
        return self.duration_minutes * 60

    def next_showtime(self, after: float) -> float | None:
        return next((t for t in self.showtimes if t >= after), None)

    def seats_left(self, showtime: float) -> int:
        return self.capacity - len(self.audience.get(showtime, ()))


@dataclass(eq=False, kw_only=True)
class Facility(Attraction):
    """Anything with servers and a line: food, drinks, restrooms, shops, first aid."""

    servers: int
    service_seconds: float
    dwell_minutes: float = 0.0
    spend: float = 0.0
    buy_chance: float = 1.0
    effects: dict[str, float] = field(default_factory=dict)
    queue: deque[Guest] = field(default_factory=deque, repr=False)
    busy: int = 0

    def estimated_wait_seconds(self) -> float:
        if self.busy < self.servers and not self.queue:
            return 0.0
        return (len(self.queue) + 1) / self.servers * self.service_seconds


_CLASSES: dict[Category, type[Attraction]] = {Category.RIDE: Ride, Category.SHOW: Show}


def build_attraction(spec: Mapping[str, Any]) -> Attraction:
    """Create an attraction from a placement spec: template + position + optional overrides."""
    try:
        attraction_id = spec["id"]
        template_key = spec["template"]
        x, y = int(spec["x"]), int(spec["y"])
        entrance = (int(spec["entrance"][0]), int(spec["entrance"][1]))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ParkError(
            f"attraction spec {dict(spec)!r} is missing or has a bad field: {exc}"
        ) from exc
    if template_key not in TEMPLATES:
        raise ParkError(f"'{attraction_id}': unknown template '{template_key}'")

    template = TEMPLATES[template_key]
    params = {k: v for k, v in template.items() if k not in META_KEYS}
    params.update(spec.get("overrides", {}))
    category = Category(template["category"])
    if category is Category.SHOW:
        params["showtimes"] = sorted(parse_clock(t) for t in params["showtimes"])
    if "effects" in params:
        params["effects"] = dict(params["effects"])

    cls = _CLASSES.get(category, Facility)
    try:
        return cls(
            id=attraction_id,
            name=spec.get("name", template["label"]),
            template=template_key,
            category=category,
            x=x,
            y=y,
            w=int(spec.get("w", template["size"][0])),
            h=int(spec.get("h", template["size"][1])),
            entrance=entrance,
            spec=dict(spec),
            **params,
        )
    except TypeError as exc:
        raise ParkError(f"'{attraction_id}': invalid override ({exc})") from exc
