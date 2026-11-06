"""Standalone transport values; no Home Assistant dependency."""
from dataclasses import dataclass
from datetime import datetime
import math
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConfigurationError(ValueError):
    """An actionable route configuration error."""


@dataclass(frozen=True)
class Stop:
    group: str
    post: str
    name: str
    lat: float
    lon: float

    def __post_init__(self):
        if not self.group or not self.post or not self.name:
            raise ValueError('Stop group, post and name are required')
        if not math.isfinite(self.lat) or not math.isfinite(self.lon) or not (-90 <= self.lat <= 90 and -180 <= self.lon <= 180):
            raise ValueError('Invalid stop coordinates')


class RouteConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True, frozen=True)
    name: str = Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')
    start_stop: str = Field(min_length=1)
    end_stop: str = Field(min_length=1)
    start_post: str | None = Field(default=None, pattern=r'^[0-9]{2}$')
    end_post: str | None = Field(default=None, pattern=r'^[0-9]{2}$')

    def __init__(self, name=None, start_stop=None, end_stop=None, **kwargs):
        super().__init__(name=name, start_stop=start_stop, end_stop=end_stop, **kwargs)

    @model_validator(mode='after')
    def distinct_stops(self):
        if self.start_stop.casefold() == self.end_stop.casefold():
            raise ConfigurationError('Start and end stops must be distinct')
        return self


@dataclass(frozen=True)
class Departure:
    time: str
    brigade: str
    direction: str = ''
    trip: str = ''


@dataclass(frozen=True)
class Vehicle:
    line: str
    brigade: str
    vehicle_id: str
    lat: float
    lon: float
    time: datetime


@dataclass(frozen=True)
class ScheduledDeparture:
    line: str
    time: datetime
    brigade: str


@dataclass(frozen=True)
class ApproachingBus:
    line: str
    vehicle_id: str
    distance_km: float
    observed_at: datetime


@dataclass(frozen=True)
class RouteResult:
    name: str
    start: Stop
    end: Stop
    lines: list[str]
    next_departures: list[ScheduledDeparture] | None
    approaching: ApproachingBus | None
    direction_status: str = 'unknown'
