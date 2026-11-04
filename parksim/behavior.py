"""Guest decision-making: what to do next.

Priority order:
  1. Leave (closing time, planned departure, exhausted, miserable, storm).
  2. Urgent needs: restroom > drink > food, picking the facility with the lowest
     walk + wait cost.
  3. Fun: score every open ride and upcoming show, then pick among the best few with a
     softmax so guests are varied rather than all choosing the single top option.

Guests decide using *posted* wait times (what the park displays), not the true wait,
just like real visitors checking an app.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .attractions import Facility
from .weather import Condition

if TYPE_CHECKING:
    import random

    from .guest import Guest
    from .simulation import Simulation

NEED_THRESHOLD = {"bladder": 0.7, "thirst": 0.65, "hunger": 0.7}
NEED_THOUGHT = {"bladder": "no_restroom", "thirst": "no_drinks", "hunger": "no_food"}
SOFTMAX_TEMPERATURE = 0.25
TOP_CHOICES = 5


@dataclass(frozen=True, slots=True)
class Decision:
    action: str  # "go" | "rest" | "leave"
    target: str | None = None
    reason: str = ""
    showtime: float | None = None


def choose(sim: Simulation, g: Guest) -> Decision:
    now = sim.now
    if now >= sim.config.close_time:
        return Decision("leave", reason="park_closed")
    if now >= g.leave_by:
        return Decision("leave", reason="done_for_day")
    if g.energy <= 0.1:
        return Decision("leave", reason="tired")
    if g.happiness <= 20:
        sim.metrics.think("leaving_unhappy")
        return Decision("leave", reason="unhappy")
    condition = sim.weather.condition_at(now)
    if condition is Condition.THUNDERSTORM and sim.rng.random() < 0.05:
        sim.metrics.think("weather")
        return Decision("leave", reason="weather")

    for need in sorted(NEED_THRESHOLD, key=lambda n: getattr(g, n), reverse=True):
        if getattr(g, need) >= NEED_THRESHOLD[need]:
            facility = best_facility(sim, g, need)
            if facility is not None:
                return Decision("go", facility.id, reason=need)
            unaffordable = any(f.effects.get(need, 0.0) < 0 and f.is_open for f in sim.facilities)
            sim.metrics.think("too_expensive" if unaffordable else NEED_THOUGHT[need])
            g.mood(-2)

    options = fun_options(sim, g, condition)
    if not options:
        return Decision("rest", reason="nothing_appealing")
    return softmax_pick(sim.rng, options)


def best_facility(sim: Simulation, g: Guest, need: str) -> Facility | None:
    best: Facility | None = None
    best_cost = math.inf
    for f in sim.facilities:
        relief = -f.effects.get(need, 0.0)
        if relief <= 0 or not f.is_open or f.spend > g.money_left:
            continue
        walk = sim.walk_seconds(g, f)
        if walk is None:
            continue
        cost = walk + f.estimated_wait_seconds() - relief * 120
        if cost < best_cost:
            best, best_cost = f, cost
    return best


def fun_options(sim: Simulation, g: Guest, condition: Condition) -> list[tuple[float, Decision]]:
    options: list[tuple[float, Decision]] = []
    wet = condition.is_wet
    skipped_for_wait = 0

    for ride in sim.rides:
        if not ride.is_open or (ride.intensity >= 8 and g.thrill < 0.3):
            continue
        walk = sim.walk_seconds(g, ride)
        if walk is None:
            continue
        posted = sim.posted.get(ride.id) or 0
        if posted > g.patience_minutes * 1.3:
            skipped_for_wait += 1
            continue
        match = 1 - abs(g.thrill - ride.intensity / 10)
        score = (
            ride.appeal / 10
            + 1.2 * match
            - posted / g.patience_minutes
            - walk / 900
            - 0.7 * g.ridden.get(ride.id, 0)
        )
        if wet and not ride.indoor:
            score -= 0.5
        options.append((score, Decision("go", ride.id)))

    if skipped_for_wait >= 3:
        sim.metrics.think("lines_too_long")

    for show in sim.shows:
        if not show.is_open or show.id in g.ridden:
            continue
        walk = sim.walk_seconds(g, show)
        if walk is None:
            continue
        showtime = show.next_showtime(sim.now + walk)
        if showtime is None or show.seats_left(showtime) <= 0:
            continue
        lead = showtime - sim.now - walk
        lookahead = show.lookahead_minutes * 60
        if lead > lookahead:
            continue
        score = show.appeal / 10 + 0.6 - 0.8 * lead / lookahead - walk / 900
        if g.energy < 0.5:
            score += 0.5  # a seat sounds good
        if wet and not show.indoor:
            score -= 0.5
        options.append((score, Decision("go", show.id, showtime=showtime)))

    if g.rides >= 2 and sim.rng.random() < 0.15:
        for shop in sim.shops:
            walk = sim.walk_seconds(g, shop)
            if shop.is_open and walk is not None:
                options.append((0.6 - walk / 900, Decision("go", shop.id)))
    return options


def softmax_pick(rng: random.Random, options: list[tuple[float, Decision]]) -> Decision:
    top = sorted(options, key=lambda o: o[0], reverse=True)[:TOP_CHOICES]
    best = top[0][0]
    weights = [math.exp((score - best) / SOFTMAX_TEMPERATURE) for score, _ in top]
    return rng.choices([d for _, d in top], weights)[0]
