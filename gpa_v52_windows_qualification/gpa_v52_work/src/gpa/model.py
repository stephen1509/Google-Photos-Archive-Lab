from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath
from typing import Any

class Confidence(str, Enum):
    PROVEN = "proven"
    USER_CONFIRMED = "user_confirmed"
    PROBABLE = "probable"
    UNKNOWN = "unknown"
    CONFLICT = "conflict"

class DatePrecision(str, Enum):
    INSTANT = "instant"
    DAY = "day"
    MONTH = "month"
    YEAR = "year"
    RANGE = "range"
    UNKNOWN = "unknown"

@dataclass(frozen=True)
class SourceRef:
    archive: str
    path: str

    @property
    def logical_dir(self) -> str:
        return str(PurePosixPath(self.path).parent)

@dataclass
class GoogleSidecar:
    title: str | None = None
    taken_epoch: int | None = None
    creation_epoch: int | None = None
    description: str | None = None
    geo_current: tuple[float, float] | None = None
    geo_exif: tuple[float, float] | None = None
    favorited: bool | None = None
    people: list[str] = field(default_factory=list)
    url: str | None = None
    origin: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

@dataclass
class DateFact:
    precision: DatePrecision
    confidence: Confidence
    value: datetime | None = None
    year: int | None = None
    month: int | None = None
    start: datetime | None = None
    end: datetime | None = None
    timezone_known: bool = False
    source: str = "unknown"
    note: str | None = None

@dataclass
class AssetEvidence:
    source: SourceRef
    sha256: str
    size: int
    sidecar: GoogleSidecar | None = None
    date_fact: DateFact | None = None
    album_memberships: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)
