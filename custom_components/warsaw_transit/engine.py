"""Conservative timetable/direction matching, independent of Home Assistant."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import asyncio
import math
import time
from .models import ConfigurationError, RouteConfig

WARSAW = ZoneInfo('Europe/Warsaw')


def schedule_datetime(clock, service_date):
    parts = clock.split(':')
    if len(parts) not in (2, 3):
        raise ValueError('Invalid timetable clock')
    h, m, s = [int(p) for p in parts] + ([0] if len(parts) == 2 else [])
    if not (0 <= h <= 47 and 0 <= m < 60 and 0 <= s < 60):
        raise ValueError('Invalid timetable clock')
    naive = datetime.combine(service_date, datetime.min.time()) + timedelta(hours=h, minutes=m, seconds=s)
    aware = naive.replace(tzinfo=WARSAW)
    if aware.astimezone(timezone.utc).astimezone(WARSAW).replace(tzinfo=None) != naive:
        raise ValueError('Nonexistent Warsaw DST clock')
    return aware


def resolve_stops(stops, name, post=None):
    found = [s for s in stops if s.name.strip().casefold() == name.strip().casefold() and (post is None or s.post == post)]
    if not found:
        raise ConfigurationError(f'Unknown stop {name!r}, post {post!r}; use an exact stop name and available post')
    return found


def _clock_seconds(clock):
    h, m, *s = map(int, clock.split(':'))
    return h*3600+m*60+(s[0] if s else 0)


def _forward(starts, ends):
    """Match nearest same-brigade stop event, not a later return journey."""
    pairs = []
    for start in starts:
        candidates = [end for end in ends if end.brigade == start.brigade
                      and (not start.direction or not end.direction or start.direction == end.direction)
                      and (not start.trip or not end.trip or start.trip == end.trip)
                      and 0 < _clock_seconds(end.time)-_clock_seconds(start.time) <= 5400]
        if candidates:
            end = min(candidates, key=lambda d: _clock_seconds(d.time))
            intervening = any(d.brigade == start.brigade and _clock_seconds(start.time) < _clock_seconds(d.time) < _clock_seconds(end.time) for d in starts)
            if not intervening:
                pairs.append((start, end))
    return pairs


def haversine(lat1, lon1, lat2, lon2):
    a, b = math.radians(lat1), math.radians(lat2)
    dlat, dlon = b-a, math.radians(lon2-lon1)
    term = math.sin(dlat/2)**2+math.cos(a)*math.cos(b)*math.sin(dlon/2)**2
    return 6371.0088*2*math.asin(math.sqrt(min(1,max(0,term))))


class TransitEngine:
    def __init__(self, client, routes):
        self.client, self.routes = client, routes
        self._resolved = []
        self._lock = asyncio.Lock()
        self._previous = {}
        self._tables = {}
        self._table_date = None

    async def _table(self, stop, line, now):
        # API exposes today's service only. Never reuse yesterday's response.
        service_date = now.astimezone(WARSAW).date()
        if self._table_date != service_date:
            self._tables.clear()
            self._table_date = service_date
        key = (stop, line)
        cached = self._tables.get(key)
        if cached is None or time.monotonic() - cached[0] >= 900:
            rows = await self.client.timetable(stop, line)
            self._tables[key] = (time.monotonic(), rows)
        return self._tables[key][1]

    async def async_setup(self):
        if len(self.routes) != 2 or len({r.name for r in self.routes}) != 2:
            raise ConfigurationError('Configure exactly two routes with distinct names')
        stops = await self.client.stops()
        lines_cache = {}
        now = datetime.now(WARSAW)
        async def lines(stop):
            if stop not in lines_cache: lines_cache[stop] = await self.client.lines(stop)
            return lines_cache[stop]
        async def table(stop, line):
            return await self._table(stop, line, now)
        resolved = []
        for route in self.routes:
            origins = resolve_stops(stops,route.start_stop,route.start_post)
            if len(origins) != 1:
                raise ConfigurationError(f'Ambiguous origin {route.start_stop!r}; set start_post. Available: '+', '.join(f'{s.group}/{s.post}' for s in origins))
            origin = origins[0]
            destinations = resolve_stops(stops,route.end_stop,route.end_post)
            valid = []
            for end in destinations:
                if (origin.group,origin.post) == (end.group,end.post):
                    raise ConfigurationError('Start and end stops must be distinct')
                shared = sorted(set(await lines(origin)) & set(await lines(end)))
                forward = []
                for line in shared:
                    if _forward(await table(origin,line),await table(end,line)):
                        forward.append(line)
                if forward: valid.append((origin,end,forward))
            if not valid:
                raise ConfigurationError(f'No forward shared routes for {route.name!r}; check start/end posts and today\'s timetable')
            if len(valid) != 1:
                raise ConfigurationError(f'Ambiguous destination {route.end_stop!r}; set end_post. Forward posts: '+', '.join(f'{e.group}/{e.post}' for _,e,_ in valid))
            resolved.append((route,*valid[0]))
        self._resolved = resolved

    async def async_refresh(self, now=None):
        if not self._resolved:
            raise ConfigurationError('Call async_setup before refreshing')
        now = now or datetime.now(WARSAW)
        if now.tzinfo is None:
            raise ValueError('now must be timezone-aware')
        now = now.astimezone(WARSAW)
        async with self._lock:
            result = {}
            async def table(stop,line):
                return await self._table(stop, line, now)
            vehicles = await self.client.gps()
            for route,start,end,lines in self._resolved:
                events = []
                candidate_events = []
                has_forward = False
                for line in lines:
                    pairs = _forward(await table(start,line), await table(end,line))
                    has_forward = has_forward or bool(pairs)
                    for departure, arrival in pairs:
                        try:
                            # ponytail: endpoint has no service-date parameter. During
                            # the overnight window, extended hours belong to yesterday.
                            service_date = now.date()-timedelta(days=1) if now.hour < 4 and _clock_seconds(departure.time) >= 86400 else now.date()
                            dt = schedule_datetime(departure.time, service_date)
                        except ValueError:
                            continue
                        # A repeated autumn clock cannot identify a unique trip.
                        if dt.utcoffset() != dt.replace(fold=1).utcoffset(): continue
                        event = {'departure':dt.isoformat(), 'line':line, 'brigade':departure.brigade, 'headsign':departure.direction}
                        if -600 <= (dt-now).total_seconds() <= 1800:
                            candidate_events.append(event)
                        if dt >= now:
                            events.append(event)
                if not has_forward:
                    raise ConfigurationError(f'No forward shared routes remain for {route.name!r}; check posts and current timetable')
                events.sort(key=lambda d:d['departure'])
                # ponytail: schedule + radial motion estimates candidates only;
                # verified direction needs a trip feed, not an endpoint vector.
                candidates = []
                for vehicle in vehicles:
                    previous = self._previous.get(vehicle.vehicle_id)
                    if previous is None or (previous.line, previous.brigade) != (vehicle.line, vehicle.brigade):
                        continue
                    if not all(0 <= (now-v.time).total_seconds() <= 120 for v in (previous, vehicle)):
                        continue
                    elapsed = (vehicle.time-previous.time).total_seconds()
                    if not 5 <= elapsed <= 120:
                        continue
                    matching = [e for e in candidate_events if (e['line'], e['brigade']) == (vehicle.line, vehicle.brigade)]
                    if len(matching) != 1:
                        continue
                    distance = haversine(vehicle.lat, vehicle.lon, start.lat, start.lon)
                    old_distance = haversine(previous.lat, previous.lon, start.lat, start.lon)
                    movement = haversine(previous.lat, previous.lon, vehicle.lat, vehicle.lon)
                    if distance >= 10 or old_distance-distance < 0.015 or movement/elapsed*3600 >= 130:
                        continue
                    candidates.append({'vehicle_id':vehicle.vehicle_id, 'line':vehicle.line, 'brigade':vehicle.brigade,
                                       'latitude':vehicle.lat, 'longitude':vehicle.lon, 'distance_km':distance,
                                       'gps_updated':vehicle.time.isoformat(), 'direction':'approaching_candidate',
                                       'certainty':'estimated_schedule_and_motion',
                                       'warning':'Direction is not guaranteed without a trip feed; schedule and motion provide an estimate only.'})
                gps = min(candidates, key=lambda v:v['distance_km']) if candidates else None
                result[route.name] = {'schedule':events[:2] or None, 'gps':gps,
                                      'status':'approaching_candidate' if gps else 'unknown',
                                      'uncertainty_reason':'direction_estimated_without_trip_id' if gps else 'no_unambiguous_fresh_schedule_and_motion_candidate',
                                      'error':None, 'start_post':start.post, 'end_post':end.post, 'lines':lines}
            self._previous = {v.vehicle_id:v for v in vehicles if 0 <= now.timestamp()-v.time.timestamp() <= 120}
            return result
