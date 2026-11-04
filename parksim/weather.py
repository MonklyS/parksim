"""Weather for a simulated day.

The forecast is a base condition plus a list of overlay periods (later overlays win).
The default generator models a Central-Florida-style summer day: mostly sunny, with a
chance of a pop-up afternoon thunderstorm. Lightning puts outdoor rides and shows on
hold until a full ``lightning_hold`` passes with no storm.
"""

from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass, field
from enum import StrEnum

from .timeutil import HOUR, MINUTE


class Condition(StrEnum):
    CLEAR = "clear"
    CLOUDY = "cloudy"
    RAIN = "rain"
    THUNDERSTORM = "thunderstorm"

    @property
    def label(self) -> str:
        return {
            "clear": "Sunny",
            "cloudy": "Cloudy",
            "rain": "Rain",
            "thunderstorm": "Thunderstorm",
        }[self.value]

    @property
    def is_wet(self) -> bool:
        return self in (Condition.RAIN, Condition.THUNDERSTORM)


TEMP_OFFSET = {
    Condition.CLEAR: 0.0,
    Condition.CLOUDY: -2.0,
    Condition.RAIN: -6.0,
    Condition.THUNDERSTORM: -10.0,
}


@dataclass(frozen=True, slots=True)
class WeatherPeriod:
    start: float
    end: float
    condition: Condition

    def to_dict(self) -> dict[str, object]:
        return {"start": self.start, "end": self.end, "condition": self.condition.value}


@dataclass
class WeatherForecast:
    base: Condition = Condition.CLEAR
    high_temp_f: float = 90.0
    periods: list[WeatherPeriod] = field(default_factory=list)

    def add_period(self, start: float, minutes: float, condition: Condition) -> None:
        self.periods.append(WeatherPeriod(start, start + minutes * MINUTE, condition))

    def add_storm(self, start: float, minutes: float) -> None:
        """A thunderstorm followed by a lighter rain tail."""
        self.add_period(start + minutes * MINUTE, 25, Condition.RAIN)
        self.add_period(start, minutes, Condition.THUNDERSTORM)

    def condition_at(self, t: float) -> Condition:
        for period in reversed(self.periods):
            if period.start <= t < period.end:
                return period.condition
        return self.base

    def temperature_at(self, t: float) -> float:
        """Daily curve: low near 6:00, peak near 15:00, cooled by clouds and rain."""
        hour = (t / HOUR) % 24
        if 6 <= hour <= 15:
            frac = (1 - math.cos(math.pi * (hour - 6) / 9)) / 2
        elif hour > 15:
            frac = (1 + math.cos(math.pi * (hour - 15) / 9)) / 2
        else:
            frac = 0.0
        low = self.high_temp_f - 14
        return low + (self.high_temp_f - low) * frac + TEMP_OFFSET[self.condition_at(t)]

    def change_times(self, start: float, end: float) -> list[float]:
        points = {p.start for p in self.periods} | {p.end for p in self.periods}
        return sorted(t for t in points if start <= t <= end)

    def segments(self, start: float, end: float) -> list[WeatherPeriod]:
        """Merged, non-overlapping condition timeline between start and end."""
        points = sorted({start, end, *(t for t in self.change_times(start, end))})
        segs: list[WeatherPeriod] = []
        for a, b in itertools.pairwise(points):
            cond = self.condition_at(a)
            if segs and segs[-1].condition is cond:
                segs[-1] = WeatherPeriod(segs[-1].start, b, cond)
            else:
                segs.append(WeatherPeriod(a, b, cond))
        return segs

    @classmethod
    def generate(
        cls,
        rng: random.Random,
        open_time: float,
        close_time: float,
        storm_chance: float = 0.35,
        high_temp_f: float = 91.0,
    ) -> WeatherForecast:
        forecast = cls(Condition.CLOUDY if rng.random() < 0.25 else Condition.CLEAR, high_temp_f)
        if rng.random() < 0.15:  # brief morning shower
            start = open_time + rng.uniform(0.5, 3.0) * HOUR
            forecast.add_period(start, rng.uniform(15, 40), Condition.RAIN)
        if rng.random() < storm_chance:  # pop-up afternoon storm
            start = rng.uniform(13.5, 17.5) * HOUR
            start = min(start, close_time - HOUR)
            forecast.add_period(start - HOUR, 60, Condition.CLOUDY)
            forecast.add_storm(start, rng.uniform(30, 90))
        return forecast
