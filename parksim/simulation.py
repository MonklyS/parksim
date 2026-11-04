"""The simulation engine: ties the park, guests, weather and operations together.

Everything happens through scheduled events (see ``scheduler.py``):

* guests arrive through the day following an arrival curve (rope drop is busiest)
* each guest repeatedly decides what to do (``behavior.py``), walks there, waits, rides...
* each ride runs a dispatch loop that loads up to ``capacity`` guests every cycle
* rides break down at random (mean time between failures) and get repaired
* incidents (guest illness/injury, ride evacuations) close rides for inspection
* thunderstorms put outdoor rides and shows on lightning hold
* posted wait times are recalculated every few minutes from queue length and throughput
"""

from __future__ import annotations

import math
import random
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import behavior
from .attractions import Attraction, Category, Closure, Facility, Ride, Show
from .errors import ParkError
from .guest import Guest, GuestState
from .metrics import Metrics, Sample
from .park import Park
from .report import build_report
from .scheduler import Scheduler
from .timeutil import HOUR, MINUTE, clock
from .weather import Condition, WeatherForecast

# Share of the day's guests arriving in each hour after opening.
ARRIVAL_PROFILE: tuple[tuple[int, float], ...] = (
    (0, 0.22),
    (1, 0.20),
    (2, 0.16),
    (3, 0.12),
    (4, 0.10),
    (5, 0.08),
    (6, 0.06),
    (7, 0.04),
    (8, 0.02),
)

RIDE_INCIDENTS = (("guest_illness", 5), ("guest_injury", 2), ("ride_evacuation", 1))

CLOSURE_SEVERITY = {
    Closure.BREAKDOWN: "warning",
    Closure.INSPECTION: "critical",
    Closure.WEATHER: "warning",
    Closure.MANUAL: "info",
}


@dataclass
class SimConfig:
    seed: int = 42
    attendance: int = 8000
    open_time: float = 9 * HOUR
    close_time: float = 21 * HOUR
    storm_chance: float = 0.35
    high_temp_f: float = 91.0
    tile_seconds: float = 4.0  # seconds for an average guest to walk one tile
    posted_wait_padding: float = 1.1  # parks tend to post slightly high waits
    sample_minutes: float = 5.0
    lightning_hold_minutes: float = 30.0
    breakdowns: bool = True
    incidents: bool = True
    incident_rate: float = 1 / 6000  # per loaded dispatch, scaled by ride intensity
    trip_rate: float = 1e-6  # per tile walked
    max_capacity: int | None = None
    end_buffer_hours: float = 3.0  # time after close for the last guests to leave


class Simulation:
    def __init__(
        self, park: Park, config: SimConfig | None = None, weather: WeatherForecast | None = None
    ) -> None:
        self.config = cfg = config or SimConfig()
        if cfg.close_time <= cfg.open_time:
            raise ValueError("close_time must be after open_time")
        self.park = park.copy()  # each run gets clean queues and stats
        self.rng = random.Random(cfg.seed)
        self.weather = weather or WeatherForecast.generate(
            random.Random(cfg.seed + 1),
            cfg.open_time,
            cfg.close_time,
            cfg.storm_chance,
            cfg.high_temp_f,
        )
        self.clock = Scheduler(start=cfg.open_time)
        self.metrics = Metrics()
        self.guests: dict[int, Guest] = {}
        self.posted: dict[str, int | None] = {}
        self.end_time = cfg.close_time + cfg.end_buffer_hours * HOUR

        attractions = list(self.park.attractions.values())
        self.rides = [a for a in attractions if isinstance(a, Ride)]
        self.shows = [a for a in attractions if isinstance(a, Show)]
        self.facilities = [a for a in attractions if isinstance(a, Facility)]
        self.shops = [f for f in self.facilities if f.category is Category.SHOP]
        self.first_aid = [f for f in self.facilities if f.category is Category.FIRST_AID]

        self._next_guest_id = 0
        self._started = False
        self._finished = False
        self._condition: Condition | None = None
        self._lightning_token = 0

    # ====================================================================================
    # Public API
    # ====================================================================================
    @property
    def now(self) -> float:
        return self.clock.now

    def run(self) -> dict[str, Any]:
        """Simulate the whole day and return a JSON-friendly report."""
        self.start()
        self.clock.run_until(self.end_time)
        return self.report()

    def advance(self, seconds: float) -> None:
        """Step the simulation forward (for a live server streaming updates)."""
        self.start()
        self.clock.run_until(min(self.now + seconds, self.end_time))
        if self.done:
            self._finish()

    @property
    def done(self) -> bool:
        return self.now >= self.end_time

    def report(self) -> dict[str, Any]:
        """Final report. Ends the day first if it is still running."""
        self._finish()
        return build_report(self)

    def walking_position(self, g: Guest) -> tuple[float, float] | None:
        """Where a walking guest is right now, interpolated along their route (tile units)."""
        if g.state not in (GuestState.WALKING, GuestState.LEAVING) or g.walk_end <= g.walk_start:
            return None
        if g.route is None:  # computed lazily: only needed when someone is watching
            if g.state is GuestState.LEAVING:
                g.route = self.park.gate_route(g.walk_from)
            elif g.target is not None:
                g.route = self.park.route(g.target, g.walk_from)
            else:
                g.route = []
        route = g.route
        if len(route) < 2:
            return None
        progress = min(max((self.now - g.walk_start) / (g.walk_end - g.walk_start), 0.0), 1.0)
        p = progress * (len(route) - 1)
        i = min(int(p), len(route) - 2)
        f = p - i
        (x0, y0), (x1, y1) = route[i], route[i + 1]
        return x0 + (x1 - x0) * f, y0 + (y1 - y0) * f

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        cfg = self.config
        for t in self._arrival_times():
            self.clock.at(t, self._guest_arrives)
        for ride in self.rides:
            self._start_ride_loop(ride)
            self._schedule_breakdown(ride)
        for show in self.shows:
            for showtime in show.showtimes:
                if cfg.open_time <= showtime < cfg.close_time:
                    self.clock.at(showtime, self._show_start, show, showtime)
        for t in self.weather.change_times(cfg.open_time, self.end_time):
            self.clock.at(t, self._weather_check)
        self.clock.at(cfg.close_time, self._park_closes)
        self.metrics.record(self.now, "park", f"{self.park.name} is open")
        self._weather_check()
        self._sample()

    # --- "tool mode" controls: a designer can poke the running park ----------------------
    def close_attraction(self, attraction_id: str, note: str = "closed by operations") -> None:
        a = self._get(attraction_id)
        self._close(a, Closure.MANUAL, f"{a.name} {note}")

    def reopen_attraction(self, attraction_id: str) -> None:
        self._reopen(self._get(attraction_id), Closure.MANUAL)

    def force_breakdown(self, attraction_id: str, minutes: float = 30.0) -> None:
        ride = self._get(attraction_id)
        if not isinstance(ride, Ride):
            raise ParkError(f"'{attraction_id}' is not a ride")
        self._break(ride, minutes)

    def trigger_storm(self, minutes: float = 45.0, start: float | None = None) -> None:
        start = self.now if start is None else start
        self.weather.add_storm(start, minutes)
        for t in (start, start + minutes * MINUTE, start + (minutes + 25) * MINUTE):
            self.clock.at(t, self._weather_check)

    def snapshot(self) -> dict[str, Any]:
        """Current state, shaped for a front end."""
        return {
            "time": self.now,
            "clock": clock(self.now),
            "weather": self.weather.condition_at(self.now).value,
            "temperature_f": round(self.weather.temperature_at(self.now)),
            "guests_in_park": len(self.guests),
            "attractions": [
                {
                    "id": a.id,
                    "name": a.name,
                    "category": a.category.value,
                    "status": a.status,
                    "posted_wait": self.posted.get(a.id),
                    "queue": len(a.queue) if isinstance(a, Ride | Facility) else 0,
                }
                for a in self.park.attractions.values()
            ],
            "guests": [
                {
                    "id": g.id,
                    "x": g.pos[0],
                    "y": g.pos[1],
                    "state": g.state.value,
                    "happiness": round(g.happiness),
                }
                for g in self.guests.values()
            ],
        }

    def walk_seconds(self, g: Guest, a: Attraction) -> float | None:
        tiles = self.park.distance(a.id, g.pos)
        return None if tiles < 0 else tiles * self.config.tile_seconds * g.walk_factor

    # ====================================================================================
    # Guests
    # ====================================================================================
    def _arrival_times(self) -> list[float]:
        cfg = self.config
        hours = [h for h, _ in ARRIVAL_PROFILE]
        weights = [w for _, w in ARRIVAL_PROFILE]
        last_entry = cfg.close_time - HOUR
        times = []
        for h in self.rng.choices(hours, weights, k=cfg.attendance):
            t = cfg.open_time + (h + self.rng.random()) * HOUR
            if t < last_entry:
                times.append(t)
        return times

    def _guest_arrives(self) -> None:
        cfg, rng = self.config, self.rng
        if cfg.max_capacity is not None and len(self.guests) >= cfg.max_capacity:
            self.metrics.turned_away += 1
            return
        now = self.now
        stay = min(max(rng.gauss(7.5, 1.5), 3.0), 12.0) * HOUR
        g = Guest(
            id=self._next_guest_id,
            arrived_at=now,
            leave_by=min(now + stay, cfg.close_time),
            pos=self.park.entrance,
            thrill=rng.betavariate(2, 2),
            patience_minutes=max(15.0, rng.gauss(60, 20)),
            budget=max(20.0, rng.gauss(90, 35)),
            walk_factor=min(max(rng.gauss(1.0, 0.15), 0.7), 1.5),
            happiness=min(max(rng.gauss(78, 6), 50.0), 95.0),
            hunger=rng.uniform(0.05, 0.4),
            thirst=rng.uniform(0.05, 0.3),
            bladder=rng.uniform(0.05, 0.3),
            updated_at=now,
        )
        self._next_guest_id += 1
        self.guests[g.id] = g
        self.metrics.arrivals += 1
        self.metrics.peak_in_park = max(self.metrics.peak_in_park, len(self.guests))
        self._decide(g)

    def _heat_factor(self) -> float:
        return 1.0 + max(0.0, self.weather.temperature_at(self.now) - 85.0) / 15.0

    def _wake(self, g: Guest, delay: float, fn: Callable[..., None], *args: Any) -> None:
        """Schedule the guest's next step; any earlier pending step is invalidated."""
        g.token += 1
        self.clock.after(delay, self._resume, g, g.token, fn, args)

    def _resume(self, g: Guest, token: int, fn: Callable[..., None], args: tuple) -> None:
        if g.token == token and g.state is not GuestState.GONE:
            fn(g, *args)

    def _decide(self, g: Guest) -> None:
        g.state = GuestState.DECIDING
        g.target = None
        g.advance(self.now, self._heat_factor())
        if self._heat_illness(g):
            return
        decision = behavior.choose(self, g)
        if decision.action == "leave":
            self._leave(g, decision.reason)
        elif decision.action == "rest":
            g.state = GuestState.RESTING
            g.energy = min(1.0, g.energy + 0.03)
            self._wake(g, self.rng.uniform(5, 12) * MINUTE, self._decide)
        else:
            assert decision.target is not None
            self._walk_to(g, self.park.attractions[decision.target], decision.showtime)

    def _walk_to(self, g: Guest, a: Attraction, showtime: float | None = None) -> None:
        tiles = max(self.park.distance(a.id, g.pos), 0)
        seconds = tiles * self.config.tile_seconds * g.walk_factor
        g.state = GuestState.WALKING
        g.target = a.id
        g.plan_showtime = showtime
        g.posted_seen = self.posted.get(a.id)
        g.energy = max(0.0, g.energy - tiles * 0.0005)
        self._begin_walk(g, seconds)
        if self.config.incidents and self.rng.random() < tiles * self.config.trip_rate:
            self._wake(g, seconds * self.rng.random(), self._trip)
            return
        self._wake(g, seconds, self._arrive, a)

    def _arrive(self, g: Guest, a: Attraction) -> None:
        g.pos = a.entrance
        if g.needs_care and isinstance(a, Facility):
            self._arrive_facility(g, a)
            return
        closed_for_day = isinstance(a, Ride) and self.now >= self.config.close_time
        if not a.is_open or closed_for_day:
            self.metrics.think("arrived_to_closed")
            g.mood(-5)
            self._decide(g)
        elif isinstance(a, Ride):
            self._join_queue(g, a)
        elif isinstance(a, Show):
            self._take_seat(g, a)
        elif isinstance(a, Facility):
            self._arrive_facility(g, a)

    def _leave(self, g: Guest, reason: str) -> None:
        g.state = GuestState.LEAVING
        g.target = None
        g.leave_reason = reason
        tiles = max(self.park.gate_distance(g.pos), 0)
        seconds = tiles * self.config.tile_seconds * g.walk_factor
        self._begin_walk(g, seconds)
        self._wake(g, seconds, self._exit)

    def _begin_walk(self, g: Guest, seconds: float) -> None:
        g.walk_from = g.pos
        g.walk_start = self.now
        g.walk_end = self.now + seconds
        g.route = None

    def _exit(self, g: Guest) -> None:
        g.state = GuestState.GONE
        self.guests.pop(g.id, None)
        self.metrics.record_exit(g)

    # ====================================================================================
    # Rides
    # ====================================================================================
    def _join_queue(self, g: Guest, ride: Ride) -> None:
        g.state = GuestState.QUEUED
        g.joined_at = self.now
        g.token += 1
        ride.queue.append(g)

    def _start_ride_loop(self, ride: Ride) -> None:
        ride.loop_token += 1
        self.clock.after(ride.cycle_seconds, self._dispatch, ride, ride.loop_token)

    def _dispatch(self, ride: Ride, token: int) -> None:
        if token != ride.loop_token or not ride.is_open:
            return
        boarded: list[Guest] = []
        while ride.queue and len(boarded) < ride.capacity:
            g = ride.queue.popleft()
            if g.state is not GuestState.QUEUED or g.target != ride.id:
                continue
            wait = self.now - g.joined_at
            ride.stats.record_wait(wait)
            if g.posted_seen is not None:
                ride.stats.posted_error_total += g.posted_seen - wait / 60
                ride.stats.posted_samples += 1
            g.last_wait = wait
            g.state = GuestState.RIDING
            boarded.append(g)
            self._wake(g, ride.ride_seconds, self._finish_ride, ride)
        if boarded:
            ride.stats.dispatches += 1
            ride.stats.guests_served += len(boarded)
            self.metrics.total_rides += len(boarded)
            risk = self.config.incident_rate * (0.5 + ride.intensity / 10)
            if self.config.incidents and self.rng.random() < risk:
                self._ride_incident(ride, boarded)
        if ride.is_open and (self.now < self.config.close_time or ride.queue):
            self.clock.after(ride.cycle_seconds, self._dispatch, ride, token)

    def _finish_ride(self, g: Guest, ride: Ride) -> None:
        g.advance(self.now, self._heat_factor())
        g.ridden[ride.id] = g.ridden.get(ride.id, 0) + 1
        g.rides += 1
        match = 1 - abs(g.thrill - ride.intensity / 10)
        # Diminishing returns: a repeat ride is less exciting, and so is the 12th ride of the day.
        novelty = 0.6 ** (g.ridden[ride.id] - 1)
        fatigue = 1 / (1 + 0.1 * (g.rides - 1))
        thrill = (1 + ride.appeal * 0.5 * match) * novelty * fatigue
        wait_minutes = g.last_wait / 60
        enjoyment = thrill - wait_minutes / 30  # every half hour in line costs a point
        if wait_minutes > g.patience_minutes:
            enjoyment -= (wait_minutes - g.patience_minutes) / 5
            self.metrics.think("waited_too_long")
        if ride.intensity >= 6 and g.thrill < 0.3 and self.rng.random() < 0.3:
            enjoyment -= 5
            g.energy = max(0.0, g.energy - 0.05)
            self.metrics.think("felt_sick")
        elif thrill >= 4:
            self.metrics.think("great_ride")
        g.mood(enjoyment)
        if g.pending_care:
            self._seek_first_aid(g)
        else:
            self._decide(g)

    def _clear_ride_queue(self, ride: Ride, leave_chance: float) -> None:
        remaining: deque[Guest] = deque()
        for g in ride.queue:
            if g.state is not GuestState.QUEUED or g.target != ride.id:
                continue
            if self.rng.random() < leave_chance:
                g.mood(-4)
                self.metrics.think("ride_closed_while_waiting")
                g.state = GuestState.DECIDING
                self._wake(g, self.rng.uniform(0, 90), self._decide)
            else:
                remaining.append(g)
        ride.queue = remaining
        if remaining:
            self.clock.after(20 * MINUTE, self._queue_patience_check, ride)

    def _queue_patience_check(self, ride: Ride) -> None:
        if not ride.is_open and ride.queue:
            self._clear_ride_queue(ride, 0.6)

    # --- breakdowns & incidents ----------------------------------------------------------
    def _schedule_breakdown(self, ride: Ride) -> None:
        if self.config.breakdowns and ride.mtbf_hours > 0:
            delay = self.rng.expovariate(1 / (ride.mtbf_hours * HOUR))
            self.clock.after(delay, self._breakdown_tick, ride)

    def _breakdown_tick(self, ride: Ride) -> None:
        if self.now >= self.config.close_time:
            return
        if ride.is_open:
            sigma = 0.5
            mu = math.log(ride.mttr_minutes) - sigma**2 / 2  # lognormal with mean = MTTR
            self._break(ride, self.rng.lognormvariate(mu, sigma))
        self._schedule_breakdown(ride)

    def _break(self, ride: Ride, minutes: float) -> None:
        self._close(
            ride,
            Closure.BREAKDOWN,
            f"{ride.name} is temporarily closed (technical issue, est. {minutes:.0f} min)",
        )
        self.clock.after(minutes * MINUTE, self._reopen, ride, Closure.BREAKDOWN)

    def _ride_incident(self, ride: Ride, boarded: list[Guest]) -> None:
        kinds, weights = zip(*RIDE_INCIDENTS, strict=True)
        kind = self.rng.choices(kinds, weights)[0]
        victim = self.rng.choice(boarded)
        self.metrics.incidents[kind] += 1
        if kind == "guest_illness":
            victim.mood(-15)
            victim.pending_care = True
            self.metrics.record(
                self.now,
                "incident",
                f"{ride.name}: guest felt unwell after riding, sent to First Aid",
                "warning",
                ride.id,
            )
        elif kind == "guest_injury":
            victim.mood(-25)
            victim.pending_care = True
            minutes = self.rng.uniform(20, 45)
            self.metrics.record(
                self.now,
                "incident",
                f"{ride.name}: guest reported a minor injury",
                "critical",
                ride.id,
            )
            self._inspect(ride, minutes)
        else:
            for g in boarded:
                g.mood(-10)
            minutes = self.rng.uniform(45, 90)
            self.metrics.record(
                self.now,
                "incident",
                f"{ride.name}: vehicle stopped on course, {len(boarded)} riders evacuated",
                "critical",
                ride.id,
            )
            self._inspect(ride, minutes)

    def _inspect(self, ride: Ride, minutes: float) -> None:
        self._close(
            ride,
            Closure.INSPECTION,
            f"{ride.name} closed for safety inspection (est. {minutes:.0f} min)",
        )
        self.clock.after(minutes * MINUTE, self._reopen, ride, Closure.INSPECTION)

    def _trip(self, g: Guest) -> None:
        if (where := self.walking_position(g)) is not None:
            g.pos = (round(where[0]), round(where[1]))
        self.metrics.incidents["trip_and_fall"] += 1
        g.mood(-15)
        self.metrics.record(
            self.now, "incident", f"Guest tripped and fell on a path near {g.pos}", "warning"
        )
        self._seek_first_aid(g)

    def _heat_illness(self, g: Guest) -> bool:
        if not self.config.incidents:
            return False
        if self.weather.temperature_at(self.now) < 90 or g.thirst < 0.85:
            return False
        if self.rng.random() >= 0.02:
            return False
        self.metrics.incidents["heat_exhaustion"] += 1
        g.mood(-15)
        self.metrics.record(
            self.now, "incident", f"Guest showing signs of heat exhaustion near {g.pos}", "warning"
        )
        self._seek_first_aid(g)
        return True

    def _seek_first_aid(self, g: Guest) -> None:
        g.pending_care = False
        options = [(self.walk_seconds(g, f), f) for f in self.first_aid if f.is_open]
        options = [(s, f) for s, f in options if s is not None]
        if not options:
            self.metrics.unserved_medical += 1
            self.metrics.think("no_first_aid")
            self._leave(g, "medical")
            return
        g.needs_care = True
        self._walk_to(g, min(options, key=lambda o: o[0])[1])

    # ====================================================================================
    # Shows
    # ====================================================================================
    def _take_seat(self, g: Guest, show: Show) -> None:
        showtime = g.plan_showtime
        if showtime is None or showtime < self.now or show.seats_left(showtime) <= 0:
            self.metrics.think("show_full")
            g.mood(-4)
            self._decide(g)
            return
        show.audience.setdefault(showtime, []).append(g)
        g.state = GuestState.AT_SHOW
        g.token += 1

    def _show_start(self, show: Show, showtime: float) -> None:
        if not show.is_open:
            show.stats.shows_cancelled += 1
            self.metrics.record(
                self.now,
                "closure",
                f"{show.name} {clock(showtime)} performance cancelled",
                "warning",
                show.id,
            )
            self._release_audience(show, showtime, cancelled=True)
            return
        audience = [g for g in show.audience.get(showtime, []) if self._watching(g, show)]
        show.audience[showtime] = audience
        show.live_showtime = showtime
        show.stats.performances += 1
        show.stats.guests_served += len(audience)
        self.clock.after(show.duration_seconds, self._show_end, show, showtime)

    def _show_end(self, show: Show, showtime: float) -> None:
        if show.live_showtime != showtime:
            return  # cancelled mid-performance
        show.live_showtime = None
        for g in show.audience.pop(showtime, []):
            if self._watching(g, show):
                g.ridden[show.id] = 1
                g.energy = min(1.0, g.energy + 0.15)
                g.mood(3 + show.appeal * 0.6)
                if show.appeal >= 8:
                    self.metrics.think("great_show")
                self._decide(g)

    def _release_audience(self, show: Show, showtime: float, cancelled: bool) -> None:
        for g in show.audience.pop(showtime, []):
            if self._watching(g, show):
                if cancelled:
                    g.mood(-6)
                    self.metrics.think("show_cancelled")
                g.state = GuestState.DECIDING
                self._wake(g, self.rng.uniform(0, 120), self._decide)

    @staticmethod
    def _watching(g: Guest, show: Show) -> bool:
        return g.state is GuestState.AT_SHOW and g.target == show.id

    # ====================================================================================
    # Facilities: food, restrooms, shops, first aid
    # ====================================================================================
    def _arrive_facility(self, g: Guest, f: Facility) -> None:
        if not g.needs_care:
            if f.spend > g.money_left:
                self.metrics.think("too_expensive")
                self._decide(g)
                return
            # Guests will wait for a restroom, but balk at a long food or shop line.
            balk_seconds = g.patience_minutes * 60 * 0.5
            if f.category is not Category.RESTROOM and f.estimated_wait_seconds() > balk_seconds:
                self.metrics.think("lines_too_long")
                g.mood(-2)
                self._decide(g)
                return
        g.state = GuestState.SERVICE
        g.joined_at = self.now
        g.token += 1
        if f.busy < f.servers:
            self._start_service(f, g)
        else:
            f.queue.append(g)

    def _start_service(self, f: Facility, g: Guest) -> None:
        f.busy += 1
        f.stats.record_wait(self.now - g.joined_at)
        duration = f.service_seconds * self.rng.uniform(0.6, 1.4)
        self.clock.after(duration, self._end_service, f, g)

    def _end_service(self, f: Facility, g: Guest) -> None:
        f.busy -= 1
        while f.queue:
            nxt = f.queue.popleft()
            if nxt.state is GuestState.SERVICE and nxt.target == f.id:
                self._start_service(f, nxt)
                break
        g.advance(self.now, self._heat_factor())
        g.apply(f.effects)
        f.stats.guests_served += 1
        if f.spend and self.rng.random() < f.buy_chance:
            g.spent += f.spend
            f.stats.revenue += f.spend
            self.metrics.revenue += f.spend
        if g.needs_care:
            g.needs_care = False
            self.metrics.treated += 1
            g.mood(5)
            if self.rng.random() < 0.35:
                self._leave(g, "medical")
                return
        dwell = f.dwell_minutes * MINUTE * self.rng.uniform(0.7, 1.3)
        if dwell > 0:
            g.state = GuestState.RESTING
            self._wake(g, dwell, self._decide)
        else:
            self._decide(g)

    # ====================================================================================
    # Operations: open/close, weather, sampling
    # ====================================================================================
    def _get(self, attraction_id: str) -> Attraction:
        try:
            return self.park.attractions[attraction_id]
        except KeyError:
            raise ParkError(f"no attraction '{attraction_id}'") from None

    def _close(self, a: Attraction, closure: Closure, message: str) -> None:
        was_open = a.is_open
        if closure in a.closures:
            return
        a.closures.add(closure)
        a.stats.closures[closure.value] += 1
        self.metrics.record(self.now, "closure", message, CLOSURE_SEVERITY[closure], a.id)
        if not was_open:
            return
        a.stats.closed_since = self.now
        if isinstance(a, Ride):
            a.loop_token += 1
            self._clear_ride_queue(a, 0.7 if closure is Closure.WEATHER else 0.5)
        elif isinstance(a, Show):
            if a.live_showtime is not None:
                a.stats.shows_cancelled += 1
                a.live_showtime = None
            for showtime in list(a.audience):
                self._release_audience(a, showtime, cancelled=True)
        elif isinstance(a, Facility):
            for g in a.queue:
                if g.state is GuestState.SERVICE and g.target == a.id:
                    g.state = GuestState.DECIDING
                    self._wake(g, self.rng.uniform(0, 60), self._decide)
            a.queue.clear()

    def _reopen(self, a: Attraction, closure: Closure) -> None:
        if closure not in a.closures:
            return
        a.closures.discard(closure)
        if not a.is_open:
            return
        self._account_downtime(a)
        if self.now < self.config.close_time:
            self.metrics.record(self.now, "reopen", f"{a.name} has reopened", "info", a.id)
            if isinstance(a, Ride):
                self._start_ride_loop(a)

    def _account_downtime(self, a: Attraction) -> None:
        s = a.stats
        if s.closed_since is None:
            return
        start = max(s.closed_since, self.config.open_time)
        end = min(self.now, self.config.close_time)
        if end > start:
            s.downtime += end - start
        s.closed_since = None

    def _weather_check(self) -> None:
        condition = self.weather.condition_at(self.now)
        if condition is self._condition:
            return
        previous, self._condition = self._condition, condition
        severity = "warning" if condition is Condition.THUNDERSTORM else "info"
        temp = self.weather.temperature_at(self.now)
        self.metrics.record(
            self.now, "weather", f"Weather: {condition.label}, {temp:.0f}°F", severity
        )
        if condition is Condition.THUNDERSTORM:
            self._lightning_token += 1
            for a in self._lightning_sensitive():
                self._close(a, Closure.WEATHER, f"{a.name} on lightning hold")
        elif previous is Condition.THUNDERSTORM:
            self._lightning_token += 1
            hold = self.config.lightning_hold_minutes * MINUTE
            self.clock.after(hold, self._lightning_all_clear, self._lightning_token)

    def _lightning_sensitive(self) -> list[Attraction]:
        return [a for a in (*self.rides, *self.shows) if not a.indoor]

    def _lightning_all_clear(self, token: int) -> None:
        if token != self._lightning_token:
            return  # another storm rolled in during the hold
        self.metrics.record(self.now, "weather", "Lightning all-clear", "info")
        for a in self._lightning_sensitive():
            self._reopen(a, Closure.WEATHER)

    def _posted_wait(self, ride: Ride) -> int | None:
        if not ride.is_open:
            return None
        padded = ride.estimated_wait_minutes() * self.config.posted_wait_padding
        return max(5, 5 * math.ceil(padded / 5))

    def _sample(self) -> None:
        for ride in self.rides:
            posted = self._posted_wait(ride)
            self.posted[ride.id] = posted
            if posted is not None:
                ride.stats.peak_posted = max(ride.stats.peak_posted, posted)
        self.metrics.samples.append(
            Sample(
                time=self.now,
                in_park=len(self.guests),
                condition=self.weather.condition_at(self.now).value,
                temperature_f=round(self.weather.temperature_at(self.now), 1),
                posted=dict(self.posted),
                status={r.id: r.status for r in self.rides},
            )
        )
        step = self.config.sample_minutes * MINUTE
        if self.now + step <= self.config.close_time:
            self.clock.after(step, self._sample)

    def _park_closes(self) -> None:
        self.metrics.record(self.now, "park", f"{self.park.name} is closed; queues still running")

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        for g in list(self.guests.values()):
            g.leave_reason = g.leave_reason or "end_of_simulation"
            self._exit(g)
        for a in self.park.attractions.values():
            self._account_downtime(a)
