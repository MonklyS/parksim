"""Park layout: the map, paths and placed attractions. Serialises to/from JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .attractions import Attraction, Category, build_attraction
from .errors import ParkError
from .grid import UNREACHABLE, Grid, Pos


class Park:
    def __init__(self, name: str, width: int, height: int, entrance: Pos) -> None:
        self.name = name
        self.grid = Grid(width, height)
        self.entrance = entrance
        self.paths: list[tuple[int, int, int, int]] = []
        self.attractions: dict[str, Attraction] = {}
        self._fields: dict[str, list[int]] = {}
        self._gate_field: list[int] | None = None

    # --- building ------------------------------------------------------------------------
    def add_path(self, x1: int, y1: int, x2: int, y2: int) -> None:
        self.grid.add_path_line(x1, y1, x2, y2)
        self.paths.append((x1, y1, x2, y2))
        self._invalidate()

    def add_attraction(self, spec: dict[str, Any]) -> Attraction:
        a = build_attraction(spec)
        if a.id in self.attractions:
            raise ParkError(f"duplicate attraction id '{a.id}'")
        ex, ey = a.entrance
        if not self.grid.is_path(ex, ey):
            raise ParkError(f"'{a.id}': entrance {a.entrance} must be on a path tile")
        if not any(abs(fx - ex) + abs(fy - ey) == 1 for fx, fy in a.footprint()):
            raise ParkError(f"'{a.id}': entrance {a.entrance} must touch the building's edge")
        self.grid.occupy(a.id, a.x, a.y, a.w, a.h)
        self.attractions[a.id] = a
        self._invalidate()
        return a

    def remove_attraction(self, attraction_id: str) -> None:
        if attraction_id not in self.attractions:
            raise ParkError(f"no attraction '{attraction_id}'")
        del self.attractions[attraction_id]
        self.grid.vacate(attraction_id)
        self._invalidate()

    def _invalidate(self) -> None:
        self._fields.clear()
        self._gate_field = None

    # --- pathfinding ---------------------------------------------------------------------
    def distance(self, attraction_id: str, pos: Pos) -> int:
        """Walking distance in tiles from ``pos`` to an attraction's entrance (-1 if none)."""
        field = self._fields.get(attraction_id)
        if field is None:
            field = self.grid.distance_field(self.attractions[attraction_id].entrance)
            self._fields[attraction_id] = field
        return field[self.grid.index(*pos)]

    def gate_distance(self, pos: Pos) -> int:
        if self._gate_field is None:
            self._gate_field = self.grid.distance_field(self.entrance)
        return self._gate_field[self.grid.index(*pos)]

    def route(self, attraction_id: str, pos: Pos) -> list[Pos]:
        self.distance(attraction_id, pos)  # ensure field cached
        return self.grid.route(self._fields[attraction_id], pos)

    def gate_route(self, pos: Pos) -> list[Pos]:
        self.gate_distance(pos)  # ensure field cached
        assert self._gate_field is not None
        return self.grid.route(self._gate_field, pos)

    # --- checks --------------------------------------------------------------------------
    def warnings(self) -> list[str]:
        """Design problems that are legal but will hurt the guest experience."""
        issues = [
            f"'{a.name}' cannot be reached from the park entrance"
            for a in self.attractions.values()
            if self.distance(a.id, self.entrance) == UNREACHABLE
        ]
        present = {a.category for a in self.attractions.values()}
        for category, message in (
            (Category.RIDE, "no rides"),
            (Category.FOOD, "no food or drink"),
            (Category.RESTROOM, "no restrooms"),
            (Category.FIRST_AID, "no First Aid station (injured guests will have to leave)"),
        ):
            if category not in present:
                issues.append(f"Park has {message}")
        return issues

    # --- serialisation -------------------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Park:
        try:
            park = cls(
                data["name"],
                int(data["width"]),
                int(data["height"]),
                (int(data["entrance"][0]), int(data["entrance"][1])),
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ParkError(f"park is missing or has a bad field: {exc}") from exc
        for segment in data.get("paths", []):
            park.add_path(*segment)
        if not park.grid.is_path(*park.entrance):
            raise ParkError(f"park entrance {park.entrance} must be on a path tile")
        for spec in data.get("attractions", []):
            park.add_attraction(spec)
        return park

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "width": self.grid.width,
            "height": self.grid.height,
            "entrance": list(self.entrance),
            "paths": [list(p) for p in self.paths],
            "attractions": [dict(a.spec) for a in self.attractions.values()],
        }

    def copy(self) -> Park:
        """A fresh park with the same layout and clean operational state."""
        return Park.from_dict(self.to_dict())

    @classmethod
    def load(cls, path: str | Path) -> Park:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
