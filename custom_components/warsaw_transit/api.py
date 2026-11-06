"""Warsaw's current POST API; one serialized request and no retry bursts."""
import asyncio
import math
import logging

_LOGGER = logging.getLogger(__name__)
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
from aiohttp import ClientError, ClientTimeout
from .models import Stop, Departure, Vehicle, ConfigurationError


class TransitError(Exception):
    def __init__(self, message, retry_after=0):
        super().__init__(message)
        self.retry_after = retry_after


class AuthError(TransitError):
    pass


class CooldownError(TransitError):
    pass


APIError = TransitError


def _rows(payload):
    if isinstance(payload, dict):
        payload = payload.get('result')
    if not isinstance(payload, list):
        raise ValueError('Expected list response')
    return payload


def _fold(row):
    pairs = row.get('values') if isinstance(row, dict) else row
    if not isinstance(pairs, list) or not all(isinstance(p, dict) and 'key' in p and 'value' in p for p in pairs):
        raise ValueError('Expected key/value row')
    return {p['key']: p['value'] for p in pairs}


class TransitClient:
    BASE = 'https://dane.um.warszawa.pl/api/action/'

    def __init__(self, session, key):
        if not isinstance(key, str) or not key.strip() or '\n' in key or '\r' in key:
            raise ConfigurationError('A nonempty raw Warsaw JWT API key is required')
        self.session, self._key = session, key.strip()
        self._lock = asyncio.Lock()
        self._failures, self._blocked_until = 0, 0.0
        self._last_request = 0.0
        self.invalid_stop_count = 0
        self.invalid_gps_count = 0

    async def _request(self, action, body, parse):
        async with self._lock:
            now = time.monotonic()
            if now < self._blocked_until:
                raise CooldownError('Warsaw API cooldown active', self._blocked_until-now)
            # Serialize and space calls, including calls from concurrent refreshes.
            await asyncio.sleep(max(0, 0.2 - (now-self._last_request)))
            kwargs = {'headers': {'Authorization': self._key}, 'timeout': ClientTimeout(total=20)}
            if body is not None:
                kwargs['json'] = body
            retry, auth = 0, False
            try:
                self._last_request = time.monotonic()
                async with self.session.post(self.BASE+action, **kwargs) as response:
                    if response.status != 200:
                        auth = response.status in (401, 403)
                        raw = response.headers.get('Retry-After', '')
                        try:
                            retry = max(0, float(raw))
                            if not math.isfinite(retry): retry = 0
                        except (ValueError, TypeError):
                            try:
                                retry = max(0, (parsedate_to_datetime(raw)-datetime.now(timezone.utc)).total_seconds())
                            except (ValueError, TypeError, OverflowError): pass
                        raise TransitError(f'Warsaw API HTTP {response.status}')
                    result = parse(await response.json(content_type=None))
            except (TransitError, ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError, OverflowError):
                self._failures += 1
                delay = max(min(900, 30 * 2**min(self._failures-1, 5)), retry, 900 if auth else 0)
                self._blocked_until = time.monotonic()+delay
                error = AuthError if auth else TransitError
                # Never include upstream payloads or credentials in exceptions.
                raise error('Warsaw API authorization failed; check the raw JWT key' if auth else 'Warsaw API request failed or returned an invalid payload', delay) from None
            self._failures = 0
            return result

    async def stops(self):
        def parse(data):
            result = []
            self.invalid_stop_count = 0
            rows = _rows(data)
            for row in rows:
                try:
                    d = _fold(row)
                    fields = [d['zespol'], d['slupek'], d['nazwa_zespolu']]
                    if not all(isinstance(v, str) and v.strip() for v in fields):
                        raise ValueError('Invalid stop identity')
                    result.append(Stop(*fields, float(d['szer_geo']), float(d['dlug_geo'])))
                except (ValueError, TypeError, KeyError, OverflowError):
                    self.invalid_stop_count += 1
            if self.invalid_stop_count:
                _LOGGER.warning('Skipped %d unusable stop rows', self.invalid_stop_count)
            if rows and not result:
                raise ValueError('No usable stops')
            return result
        return await self._request('get_ztm_przystanki_komunikacji_miejskiej', None, parse)

    async def lines(self, stop):
        def parse(data):
            lines = [_fold(row)['linia'] for row in _rows(data)]
            if not all(isinstance(line, str) and line.strip() for line in lines):
                raise ValueError('Invalid line')
            return sorted(set(lines))
        # Verified in dataset 8eb065b6-f3a0-41d4-a842-732c6c1c3063 call_examples.
        return await self._request('get_ztm_lista_linii_na_przystanku', {'busstopId':stop.group, 'busstopNr':stop.post}, parse)

    async def timetable(self, stop, line):
        def parse(data):
            from .engine import schedule_datetime
            result = []
            for row in _rows(data):
                d = _fold(row)
                clock, brigade = d['czas'], d['brygada']
                if not isinstance(clock, str) or not isinstance(brigade, str) or not brigade:
                    raise ValueError('Invalid timetable')
                schedule_datetime(clock, datetime(2026, 1, 1).date())
                result.append(Departure(clock, brigade, str(d.get('kierunek', '')), str(d.get('trasa', ''))))
            return result
        return await self._request('get_ztm_odjazdy_linii_z_przystanku', {'busstopId':stop.group, 'busstopNr':stop.post, 'line':line}, parse)

    async def gps(self):
        def parse(data):
            result = []
            self.invalid_gps_count = 0
            rows = _rows(data)
            for d in rows:
                try:
                    if not isinstance(d, dict): raise ValueError('Invalid GPS row')
                    lat, lon = float(d['Lat']), float(d['Lon'])
                    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
                        raise ValueError('Invalid GPS coordinate')
                    stamp = datetime.fromisoformat(d['Time'])
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=ZoneInfo('Europe/Warsaw'))
                        if stamp.utcoffset() != stamp.replace(fold=1).utcoffset():
                            raise ValueError('Ambiguous GPS clock')
                        if stamp.astimezone(timezone.utc).astimezone(stamp.tzinfo).replace(tzinfo=None) != stamp.replace(tzinfo=None):
                            raise ValueError('Nonexistent GPS clock')
                    fields = [d['Lines'], d['Brigade'], d['VehicleNumber']]
                    if not all(type(v) in (str, int) and str(v).strip() for v in fields):
                        raise ValueError('Invalid vehicle identity')
                    result.append(Vehicle(*(str(v) for v in fields), lat, lon, stamp))
                except (ValueError, TypeError, KeyError, OverflowError):
                    self.invalid_gps_count += 1
            if self.invalid_gps_count:
                _LOGGER.warning('Skipped %d unusable GPS rows', self.invalid_gps_count)
            if rows and not result:
                raise ValueError('No usable GPS rows')
            return result
        return await self._request('get_ztm_lokalizacja_pojazdow', {'type':1}, parse)


WarsawAPI = TransitClient
