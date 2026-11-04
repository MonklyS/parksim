"""Server-side live sessions reuse the engine's LiveSession (the browser runs the same code)."""

from __future__ import annotations

from parksim import Park
from parksim.live import TICK_SECONDS, DaySettings
from parksim.live import LiveSession as _EngineSession

from .models import SimSettings

__all__ = ["TICK_SECONDS", "LiveSession"]


class LiveSession(_EngineSession):
    def __init__(self, park: Park, settings: SimSettings) -> None:
        super().__init__(park, DaySettings(**settings.model_dump()))
