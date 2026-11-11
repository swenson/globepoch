"""The map catalog: which map is shown on which day.

The catalog lives in ``catalog.toml`` next to this file. Each ``[[map]]`` entry either has a
``date`` (it is *scheduled* for that day) or no date (it sits in the *backlog*). Scheduled
maps must cover every day from the catalog ``epoch`` without gaps; once the schedule runs
out, the backlog is used in file order, one map per day.
"""

from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on Python 3.10
    import tomli as tomllib

CATALOG_PATH = Path(__file__).with_name("catalog.toml")

Box = tuple[float, float, float, float]


class CatalogError(ValueError):
    """The catalog is malformed or cannot provide a map for the requested day."""


@dataclass(frozen=True)
class MapEntry:
    id: str
    year: int
    title: str
    creator: str
    place: str
    source_page: str
    credit: str
    blurb: str
    #: IIIF Image API service URL (preferred: fetched as tiles and stitched together) …
    iiif: str = ""
    #: … or a direct link to the scan.
    image_url: str = ""
    date: dt.date | None = None
    #: Fractional (left, top, right, bottom) box to keep, applied before anything else.
    crop: Box | None = None
    #: Fractional boxes (relative to the cropped image) painted over before tiling, so that
    #: printed dates and other giveaways cannot be read.
    redact: tuple[Box, ...] = ()
    #: Longest side, in pixels, of the image that gets tiled.
    max_px: int = 10000

    @classmethod
    def from_dict(cls, raw: dict) -> MapEntry:
        data = dict(raw)
        if "date" in data and not isinstance(data["date"], dt.date):
            data["date"] = dt.date.fromisoformat(str(data["date"]))
        if "crop" in data:
            data["crop"] = _box(data["crop"])
        data["redact"] = tuple(_box(b) for b in data.get("redact", ()))
        try:
            return cls(**data)
        except TypeError as exc:
            raise CatalogError(f"bad map entry {raw.get('id', '?')!r}: {exc}") from None


def _box(values) -> Box:
    box = tuple(float(v) for v in values)
    if len(box) != 4:
        raise CatalogError(f"box must have 4 numbers, got {values!r}")
    left, top, right, bottom = box
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise CatalogError(f"box must be fractions with left<right and top<bottom: {values!r}")
    return box


@dataclass
class Catalog:
    epoch: dt.date
    maps: list[MapEntry]
    min_year: int = 1400
    by_date: dict[dt.date, MapEntry] = field(init=False)
    backlog: list[MapEntry] = field(init=False)

    def __post_init__(self) -> None:
        self.by_date = {m.date: m for m in self.maps if m.date is not None}
        self.backlog = [m for m in self.maps if m.date is None]
        self.validate()

    @classmethod
    def load(cls, path: Path = CATALOG_PATH) -> Catalog:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict) -> Catalog:
        epoch = raw["epoch"]
        if not isinstance(epoch, dt.date):
            epoch = dt.date.fromisoformat(str(epoch))
        maps = [MapEntry.from_dict(m) for m in raw.get("map", [])]
        return cls(epoch=epoch, maps=maps, min_year=int(raw.get("min_year", 1400)))

    def validate(self) -> None:
        ids = [m.id for m in self.maps]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise CatalogError(f"duplicate map ids: {sorted(dupes)}")
        dated = [m.date for m in self.maps if m.date is not None]
        if len(dated) != len(set(dated)):
            raise CatalogError("two maps are scheduled for the same date")
        if dated:
            if min(dated) != self.epoch:
                raise CatalogError(f"schedule must start on the epoch {self.epoch}")
            span = (max(dated) - self.epoch).days + 1
            if span != len(dated):
                missing = [
                    self.epoch + dt.timedelta(days=i)
                    for i in range(span)
                    if self.epoch + dt.timedelta(days=i) not in self.by_date
                ]
                raise CatalogError(f"schedule has gaps: {[d.isoformat() for d in missing]}")
        for m in self.maps:
            if bool(m.iiif) == bool(m.image_url):
                raise CatalogError(f"{m.id}: give exactly one of `iiif` or `image_url`")
            if not self.min_year <= m.year <= dt.date.today().year:
                raise CatalogError(f"{m.id}: year {m.year} is outside the guessable range")

    @property
    def last_scheduled(self) -> dt.date:
        return max(self.by_date, default=self.epoch - dt.timedelta(days=1))

    def puzzle_number(self, day: dt.date) -> int:
        return (day - self.epoch).days + 1

    def map_for(self, day: dt.date) -> MapEntry:
        if day < self.epoch:
            raise CatalogError(f"{day} is before the first puzzle ({self.epoch})")
        if day in self.by_date:
            return self.by_date[day]
        index = (day - self.last_scheduled).days - 1
        if index >= len(self.backlog):
            raise CatalogError(
                f"no map for {day}: the schedule ends {self.last_scheduled} and the backlog "
                f"only has {len(self.backlog)} maps. Add more maps to catalog.toml."
            )
        return self.backlog[index]

    def get(self, map_id: str) -> MapEntry:
        for m in self.maps:
            if m.id == map_id:
                return m
        raise CatalogError(f"no map with id {map_id!r}")

    def upcoming(self, start: dt.date, days: int) -> list[tuple[dt.date, int, MapEntry | None]]:
        rows = []
        for i in range(days):
            day = start + dt.timedelta(days=i)
            try:
                entry = self.map_for(day)
            except CatalogError:
                entry = None
            rows.append((day, self.puzzle_number(day), entry))
        return rows
