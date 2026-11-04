"""Tile grid for the park map, plus BFS distance fields for guest pathfinding.

Rather than running A* for every guest trip, each attraction gets a precomputed
*distance field*: the walking distance (in tiles) from every path tile to that
attraction's entrance. A guest's travel time is then an O(1) lookup, and the actual
route is recovered by stepping "downhill" through the field.
"""

from __future__ import annotations

from collections import deque

from .errors import ParkError

Pos = tuple[int, int]
UNREACHABLE = -1


class Grid:
    def __init__(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            raise ParkError("park width and height must be positive")
        self.width = width
        self.height = height
        self._path = bytearray(width * height)
        self._owner: list[str | None] = [None] * (width * height)

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def index(self, x: int, y: int) -> int:
        return y * self.width + x

    def is_path(self, x: int, y: int) -> bool:
        return self.in_bounds(x, y) and bool(self._path[self.index(x, y)])

    def owner(self, x: int, y: int) -> str | None:
        return self._owner[self.index(x, y)] if self.in_bounds(x, y) else None

    def add_path_line(self, x1: int, y1: int, x2: int, y2: int) -> None:
        """Lay a straight horizontal or vertical path segment (inclusive)."""
        if x1 != x2 and y1 != y2:
            raise ParkError(f"path ({x1},{y1})-({x2},{y2}) must be horizontal or vertical")
        tiles = [
            (x, y)
            for x in range(min(x1, x2), max(x1, x2) + 1)
            for y in range(min(y1, y2), max(y1, y2) + 1)
        ]
        for x, y in tiles:
            if not self.in_bounds(x, y):
                raise ParkError(f"path tile ({x},{y}) is outside the park")
            if (owner := self.owner(x, y)) is not None:
                raise ParkError(f"path tile ({x},{y}) overlaps '{owner}'")
        for x, y in tiles:
            self._path[self.index(x, y)] = 1

    def occupy(self, owner: str, x: int, y: int, w: int, h: int) -> None:
        """Claim a rectangular footprint for a building."""
        if w <= 0 or h <= 0:
            raise ParkError(f"'{owner}' must have a positive size")
        tiles = [(tx, ty) for tx in range(x, x + w) for ty in range(y, y + h)]
        for tx, ty in tiles:
            if not self.in_bounds(tx, ty):
                raise ParkError(f"'{owner}' extends outside the park at ({tx},{ty})")
            if self.is_path(tx, ty):
                raise ParkError(f"'{owner}' overlaps a path at ({tx},{ty})")
            if (other := self.owner(tx, ty)) is not None:
                raise ParkError(f"'{owner}' overlaps '{other}' at ({tx},{ty})")
        for tx, ty in tiles:
            self._owner[self.index(tx, ty)] = owner

    def vacate(self, owner: str) -> None:
        self._owner = [None if o == owner else o for o in self._owner]

    def _walkable_neighbors(self, i: int):
        y, x = divmod(i, self.width)
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if self.is_path(nx, ny):
                yield self.index(nx, ny)

    def distance_field(self, target: Pos) -> list[int]:
        """BFS outward from ``target``: tiles-to-walk from every path tile to it."""
        dist = [UNREACHABLE] * (self.width * self.height)
        if not self.is_path(*target):
            return dist
        start = self.index(*target)
        dist[start] = 0
        frontier = deque([start])
        while frontier:
            i = frontier.popleft()
            for n in self._walkable_neighbors(i):
                if dist[n] == UNREACHABLE:
                    dist[n] = dist[i] + 1
                    frontier.append(n)
        return dist

    def route(self, field: list[int], start: Pos) -> list[Pos]:
        """Tiles from ``start`` to the field's target (inclusive); empty if unreachable."""
        i = self.index(*start)
        if field[i] == UNREACHABLE:
            return []
        steps = [start]
        while field[i] > 0:
            i = next(n for n in self._walkable_neighbors(i) if field[n] == field[i] - 1)
            steps.append((i % self.width, i // self.width))
        return steps
