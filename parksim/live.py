"""A live simulation session: advance in real time, take operations commands, emit frames.

This lives in the engine (not the server) so exactly the same code drives the day whether
it runs on the FastAPI server or inside the browser via Pyodide.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from .errors import ParkError
from .park import Park
from .simulation import SimConfig, Simulation
from .view import frame

SPEEDS = (1, 10, 60, 300, 1200)  # simulated seconds per real second
DEFAULT_SPEED = 60
TICK_SECONDS = 0.5
MAX_TICK_SECONDS = 1.0  # never jump further than this after a slow tick


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    try:
        return min(max(float(value), low), high)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class DaySettings:
    """What a viewer can choose before opening the gates (same limits as the API)."""

    seed: int | None = None
    attendance: int = 8000
    storm_chance: float = 0.35
    high_temp_f: float = 91.0
    breakdowns: bool = True
    incidents: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> DaySettings:
        data = data or {}
        seed = data.get("seed")
        return cls(
            seed=None if seed is None else int(_clamp(seed, 0, 2**31, 0)),
            attendance=int(_clamp(data.get("attendance"), 100, 30_000, 8000)),
            storm_chance=_clamp(data.get("storm_chance"), 0, 1, 0.35),
            high_temp_f=_clamp(data.get("high_temp_f"), 50, 110, 91.0),
            breakdowns=bool(data.get("breakdowns", True)),
            incidents=bool(data.get("incidents", True)),
        )

    def to_config(self) -> SimConfig:
        return SimConfig(
            seed=self.seed if self.seed is not None else random.randrange(1_000_000),
            attendance=self.attendance,
            storm_chance=self.storm_chance,
            high_temp_f=self.high_temp_f,
            breakdowns=self.breakdowns,
            incidents=self.incidents,
        )


class LiveSession:
    def __init__(self, park: Park, settings: DaySettings | None = None) -> None:
        self.sim = Simulation(park, (settings or DaySettings()).to_config())
        self.sim.start()
        self.speed = DEFAULT_SPEED
        self.paused = False
        self.errors: list[str] = []
        self._log_index = 0

    @property
    def done(self) -> bool:
        return self.sim.done

    def init_message(self) -> dict[str, Any]:
        cfg = self.sim.config
        return {
            "type": "init",
            "seed": cfg.seed,
            "attendance": cfg.attendance,
            "open_time": cfg.open_time,
            "close_time": cfg.close_time,
            "end_time": self.sim.end_time,
            "speeds": list(SPEEDS),
            "speed": self.speed,
        }

    def handle(self, message: Any) -> None:
        """Apply a client command. Bad commands are reported, never raised."""
        if not isinstance(message, dict):
            self.errors.append("commands must be JSON objects")
            return
        kind = message.get("type")
        try:
            match kind:
                case "speed":
                    value = int(message["value"])
                    if value not in SPEEDS:
                        raise ValueError(f"speed must be one of {SPEEDS}")
                    self.speed, self.paused = value, False
                case "pause":
                    self.paused = True
                case "resume":
                    self.paused = False
                case "close":
                    self.sim.close_attraction(str(message["id"]))
                case "reopen":
                    self.sim.reopen_attraction(str(message["id"]))
                case "breakdown":
                    minutes = _clamp(message.get("minutes"), 1, 240, 30)
                    self.sim.force_breakdown(str(message["id"]), minutes)
                case "storm":
                    self.sim.trigger_storm(minutes=_clamp(message.get("minutes"), 10, 180, 45))
                case _:
                    raise ValueError(f"unknown command {kind!r}")
        except (KeyError, TypeError, ValueError, ParkError) as exc:
            self.errors.append(f"{kind}: {exc}")

    def tick(self, wall_seconds: float) -> None:
        if not self.paused and not self.done:
            self.sim.advance(min(wall_seconds, MAX_TICK_SECONDS) * self.speed)

    def frame_message(self) -> dict[str, Any]:
        data = frame(self.sim, self._log_index)
        self._log_index = len(self.sim.metrics.log)
        errors, self.errors = self.errors, []
        return {
            "type": "frame",
            "speed": self.speed,
            "paused": self.paused,
            "errors": errors,
            **data,
        }

    def report_message(self) -> dict[str, Any]:
        return {"type": "report", "report": self.sim.report()}
