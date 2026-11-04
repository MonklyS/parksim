"""Compact, render-ready frames of a running simulation for a live viewer.

Only guests who are out on the paths are sent as dots. Guests inside a building (in a
ride queue, riding, at a show, eating) show up as that building's queue and wait-time
badge instead. This keeps frames small even with thousands of guests in the park.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .attractions import Facility, Ride, Show
from .guest import Guest, GuestState
from .timeutil import clock

if TYPE_CHECKING:
    from .simulation import Simulation

VISIBLE_STATES = frozenset(
    {GuestState.WALKING, GuestState.LEAVING, GuestState.DECIDING, GuestState.RESTING}
)


def _jitter(guest_id: int, salt: int) -> float:
    """Deterministic offset in [-0.35, 0.35] so idle guests don't stack on one point."""
    return ((guest_id * 2654435761 + salt * 40503) % 1000) / 1000 * 0.7 - 0.35


def guest_point(sim: Simulation, g: Guest) -> tuple[float, float]:
    """Guest position in tile units; tile (x, y) spans x..x+1, so centres are +0.5."""
    if (walking := sim.walking_position(g)) is not None:
        x, y = walking
        return x + 0.5, y + 0.5
    x, y = g.pos
    return x + 0.5 + _jitter(g.id, 1), y + 0.5 + _jitter(g.id, 2)


def frame(sim: Simulation, log_from: int = 0) -> dict[str, Any]:
    """Everything a viewer needs to draw the current moment.

    ``guests`` is a flat list ``[id, x*10, y*10, happiness // 10, ...]`` (4 ints per guest)
    to keep the JSON small.
    """
    guests: list[int] = []
    happiness_total = 0.0
    for g in sim.guests.values():
        happiness_total += g.happiness
        if g.state in VISIBLE_STATES:
            x, y = guest_point(sim, g)
            guests += (g.id, round(x * 10), round(y * 10), min(int(g.happiness // 10), 9))

    attractions = []
    after_close = sim.now >= sim.config.close_time
    for a in sim.park.attractions.values():
        status = "closed" if after_close and isinstance(a, Ride | Show) else a.status
        entry: dict[str, Any] = {"id": a.id, "status": status}
        if isinstance(a, Ride):
            entry["posted"] = None if after_close else sim.posted.get(a.id)
            entry["queue"] = len(a.queue)
        elif isinstance(a, Show):
            entry["next_show"] = a.next_showtime(sim.now)
            entry["seated"] = sum(len(v) for v in a.audience.values())
        elif isinstance(a, Facility):
            entry["queue"] = len(a.queue)
            entry["wait"] = round(a.estimated_wait_seconds() / 60)
        attractions.append(entry)

    in_park = len(sim.guests)
    return {
        "time": sim.now,
        "clock": clock(sim.now),
        "weather": sim.weather.condition_at(sim.now).value,
        "temperature_f": round(sim.weather.temperature_at(sim.now)),
        "in_park": in_park,
        "arrivals": sim.metrics.arrivals,
        "avg_happiness": round(happiness_total / in_park, 1) if in_park else None,
        "open": sim.config.open_time <= sim.now < sim.config.close_time,
        "attractions": attractions,
        "guests": guests,
        "log": [
            {
                "time": e.time,
                "kind": e.kind,
                "severity": e.severity,
                "message": e.message,
                "attraction": e.attraction,
            }
            for e in sim.metrics.log[log_from:]
        ],
    }
