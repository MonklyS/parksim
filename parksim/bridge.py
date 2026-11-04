"""JavaScript-facing API for running the engine in the browser with Pyodide.

Everything crosses the JS/Python boundary as JSON strings: simple, fast to copy between
a Web Worker and the page, and no proxy objects to leak or free.

    session = start_live(layout_json, settings_json)   # LiveBridge
    session.tick(0.5); session.frame_json()             # every half second
    session.command_json('{"type": "storm"}')

    forecast = Forecast(layout_json, settings_json)
    while not forecast.done: forecast.step(1800)        # 30 simulated minutes per step
    forecast.report_json()
"""

from __future__ import annotations

import json
from typing import Any

from .live import DaySettings, LiveSession
from .park import Park
from .simulation import Simulation
from .timeutil import clock


def _load(layout_json: str, settings_json: str | None) -> tuple[Park, DaySettings]:
    park = Park.from_dict(json.loads(layout_json))
    settings = DaySettings.from_dict(json.loads(settings_json) if settings_json else None)
    return park, settings


def _dump(data: Any) -> str:
    return json.dumps(data, separators=(",", ":"))


def validate_json(layout_json: str) -> str:
    """Same answer as the server's POST /api/validate."""
    try:
        park = Park.from_dict(json.loads(layout_json))
    except ValueError as exc:  # ParkError and JSON errors
        return _dump({"ok": False, "error": str(exc), "warnings": []})
    return _dump({"ok": True, "error": None, "warnings": park.warnings()})


class LiveBridge:
    def __init__(self, layout_json: str, settings_json: str | None = None) -> None:
        park, settings = _load(layout_json, settings_json)
        self.session = LiveSession(park, settings)

    @property
    def done(self) -> bool:
        return self.session.done

    def init_json(self) -> str:
        return _dump(self.session.init_message())

    def tick(self, wall_seconds: float) -> None:
        self.session.tick(wall_seconds)

    def frame_json(self) -> str:
        return _dump(self.session.frame_message())

    def command_json(self, command_json: str) -> None:
        try:
            command = json.loads(command_json)
        except ValueError:
            self.session.errors.append("could not parse command")
            return
        self.session.handle(command)

    def report_json(self) -> str:
        return _dump(self.session.report_message())


def start_live(layout_json: str, settings_json: str | None = None) -> LiveBridge:
    return LiveBridge(layout_json, settings_json)


class Forecast:
    """Run a whole day as fast as possible, in steps so the caller can show progress."""

    def __init__(self, layout_json: str, settings_json: str | None = None) -> None:
        park, settings = _load(layout_json, settings_json)
        self.sim = Simulation(park, settings.to_config())
        self.sim.start()
        self._start = self.sim.now

    @property
    def done(self) -> bool:
        return self.sim.done

    @property
    def progress(self) -> float:
        return (self.sim.now - self._start) / (self.sim.end_time - self._start)

    @property
    def clock(self) -> str:
        return clock(self.sim.now)

    def step(self, sim_seconds: float = 1800) -> float:
        self.sim.advance(sim_seconds)
        return self.progress

    def report_json(self) -> str:
        return _dump(self.sim.report())
