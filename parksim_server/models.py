"""Request/response schemas. Pydantic checks shape and limits; the engine checks layout rules."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

Coord = tuple[int, int]
Segment = tuple[int, int, int, int]


class AttractionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,40}$")
    template: str = Field(max_length=40)
    name: str | None = Field(default=None, min_length=1, max_length=60)
    x: int
    y: int
    w: int | None = Field(default=None, ge=1, le=20)
    h: int | None = Field(default=None, ge=1, le=20)
    entrance: Coord


class ParkLayout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    width: int = Field(ge=8, le=120)
    height: int = Field(ge=8, le=120)
    entrance: Coord
    paths: list[Segment] = Field(default_factory=list, max_length=10_000)
    attractions: list[AttractionSpec] = Field(default_factory=list, max_length=400)

    def to_engine(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class SimSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed: int | None = Field(default=None, ge=0, le=2**31)
    attendance: int = Field(default=8000, ge=100, le=30_000)
    storm_chance: float = Field(default=0.35, ge=0, le=1)
    high_temp_f: float = Field(default=91.0, ge=50, le=110)
    breakdowns: bool = True
    incidents: bool = True


class Validation(BaseModel):
    ok: bool
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)


class CreatedPark(BaseModel):
    id: str
    edit_token: str


class StoredPark(BaseModel):
    id: str
    layout: dict[str, Any]
    warnings: list[str]
    updated_at: str
