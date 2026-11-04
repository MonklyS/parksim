"""What the simulation records: an operations log, periodic samples and guest feedback."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .guest import Guest

THOUGHT_TEXT = {
    "lines_too_long": "The lines are too long",
    "waited_too_long": "I waited way longer than I wanted to",
    "arrived_to_closed": "I walked all the way there and it was closed",
    "ride_closed_while_waiting": "The ride closed while I was in line",
    "no_restroom": "I can't find a restroom",
    "no_drinks": "I'm thirsty and there's nothing to drink nearby",
    "no_food": "I'm hungry and there's no food nearby",
    "show_full": "The show was full",
    "show_cancelled": "The show I was waiting for was cancelled",
    "felt_sick": "That ride made me feel sick",
    "great_ride": "That ride was amazing!",
    "great_show": "What a great show!",
    "too_expensive": "I can't afford anything here",
    "no_first_aid": "Nobody could help me when I got hurt",
    "leaving_unhappy": "I'm not having fun, I'm going home",
    "weather": "This storm is ruining my day",
}


@dataclass(frozen=True, slots=True)
class LogEntry:
    time: float
    kind: str  # weather | closure | reopen | incident | park
    severity: str  # info | warning | critical
    message: str
    attraction: str | None = None


@dataclass(frozen=True, slots=True)
class Sample:
    time: float
    in_park: int
    condition: str
    temperature_f: float
    posted: dict[str, int | None]
    status: dict[str, str]


@dataclass
class Metrics:
    log: list[LogEntry] = field(default_factory=list)
    samples: list[Sample] = field(default_factory=list)
    thoughts: Counter[str] = field(default_factory=Counter)
    incidents: Counter[str] = field(default_factory=Counter)
    exit_reasons: Counter[str] = field(default_factory=Counter)
    exit_happiness: list[float] = field(default_factory=list)
    arrivals: int = 0
    turned_away: int = 0
    peak_in_park: int = 0
    total_rides: int = 0
    revenue: float = 0.0
    treated: int = 0
    unserved_medical: int = 0

    def think(self, key: str) -> None:
        self.thoughts[key] += 1

    def record(
        self,
        time: float,
        kind: str,
        message: str,
        severity: str = "info",
        attraction: str | None = None,
    ) -> None:
        self.log.append(LogEntry(time, kind, severity, message, attraction))

    def record_exit(self, g: Guest) -> None:
        self.exit_reasons[g.leave_reason or "unknown"] += 1
        self.exit_happiness.append(g.happiness)

    def log_dicts(self) -> list[dict[str, object]]:
        return [asdict(entry) for entry in self.log]
