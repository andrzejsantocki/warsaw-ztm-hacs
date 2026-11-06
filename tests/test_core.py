import unittest
from datetime import datetime, date, timezone
from importlib.util import spec_from_file_location, module_from_spec
import sys
from pathlib import Path
import types

# Load standalone core even when the HA adapter imports Home Assistant.
ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType('custom_components.warsaw_transit')
pkg.__path__ = [str(ROOT / 'custom_components/warsaw_transit')]
sys.modules.setdefault('custom_components', types.ModuleType('custom_components'))
sys.modules.setdefault('custom_components.warsaw_transit', pkg)

class Response:
    def __init__(self, data, status=200, headers=None):
        self.data, self.status, self.headers = data, status, headers or {}
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def json(self, **kwargs): return self.data

class Session:
    def __init__(self, *responses): self.responses, self.calls = list(responses), []
    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)

def kv(**values): return {'values': [{'key': k, 'value': v} for k,v in values.items()]}

class APITests(unittest.IsolatedAsyncioTestCase):
    async def test_official_payloads_and_raw_auth(self):
        from custom_components.warsaw_transit.api import WarsawAPI
        session = Session(Response([kv(zespol='1',slupek='01',nazwa_zespolu='A',szer_geo='52',dlug_geo='21')]), Response([kv(linia='120')]), Response([[{'key':'czas','value':'25:10:00'}, {'key':'brygada','value':'1'}]]), Response({'result':[{'Lines':'120','Brigade':'1','VehicleNumber':'V','Lat':52,'Lon':21,'Time':'2026-11-06 08:00:00'}]}))
        api = WarsawAPI(session, 'test-token')
        stop = (await api.stops())[0]
        self.assertEqual(await api.lines(stop), ['120'])
        self.assertEqual((await api.timetable(stop, '120'))[0].time, '25:10:00')
        self.assertEqual((await api.gps())[0].vehicle_id, 'V')
        self.assertTrue(session.calls[1][0].endswith('/get_ztm_lista_linii_na_przystanku'))
        self.assertEqual(session.calls[0][1]['headers']['Authorization'], 'test-token')
        self.assertNotIn('json', session.calls[0][1])

    async def test_partial_stops_skip_null_coordinates_with_warning_count(self):
        from custom_components.warsaw_transit.api import WarsawAPI, APIError
        good = kv(zespol='1', slupek='01', nazwa_zespolu='A', szer_geo='52', dlug_geo='21')
        bad = kv(zespol='2', slupek='01', nazwa_zespolu='Rail', szer_geo=None, dlug_geo=None)
        api = WarsawAPI(Session(Response([bad, good, {'values':None}]), Response([bad])), 'test')
        with self.assertLogs('custom_components.warsaw_transit.api', level='WARNING') as logs:
            self.assertEqual([s.name for s in await api.stops()], ['A'])
        self.assertEqual(api.invalid_stop_count, 2)
        self.assertIn('2', logs.output[0])
        with self.assertRaises(APIError): await api.stops()

    async def test_partial_gps_skips_malformed_rows_without_cooldown(self):
        from custom_components.warsaw_transit.api import WarsawAPI, APIError
        good = {'Lines':'120','Brigade':'1','VehicleNumber':'V','Lat':52,'Lon':21,'Time':'2026-11-06 08:00:00'}
        malformed = [None, {}, dict(good, Lat=None), dict(good, Time='bad'), dict(good, Lon='nan')]
        api = WarsawAPI(Session(Response({'result':malformed + [good]}), Response([]), Response([{}])), 'test')
        with self.assertLogs('custom_components.warsaw_transit.api', level='WARNING'):
            self.assertEqual([v.vehicle_id for v in await api.gps()], ['V'])
        self.assertEqual(api.invalid_gps_count, len(malformed))
        self.assertEqual(await api.gps(), [])
        self.assertEqual(api.invalid_gps_count, 0)
        with self.assertLogs('custom_components.warsaw_transit.api', level='WARNING'):
            with self.assertRaises(APIError): await api.gps()

    async def test_cooldown_and_malformed_payload(self):
        from custom_components.warsaw_transit.api import WarsawAPI, APIError, CooldownError
        session = Session(Response([], 429, {'Retry-After':'120'}))
        api = WarsawAPI(session, 'test')
        with self.assertRaises(APIError) as caught: await api.stops()
        self.assertGreaterEqual(caught.exception.retry_after,120)
        with self.assertRaises(CooldownError): await api.gps()
        self.assertEqual(len(session.calls), 1)
        bad = WarsawAPI(Session(Response({'bad':'data'})), 'test')
        with self.assertRaises(APIError): await bad.stops()

    async def test_backoff_auth_and_shared_lock(self):
        from custom_components.warsaw_transit.api import TransitClient, TransitError, AuthError
        from unittest.mock import patch
        session = Session(Response([],500),Response([],500),Response([],401))
        client = TransitClient(session,'test')
        for expected, error in [(30,TransitError),(60,TransitError),(900,AuthError)]:
            client._blocked_until = 0
            with self.assertRaises(error) as caught: await client.gps()
            self.assertGreaterEqual(caught.exception.retry_after,expected)
        import asyncio
        active = 0
        class Delayed(Response):
            async def json(self, **kwargs):
                nonlocal active
                active += 1
                if active != 1: raise AssertionError('Concurrent API requests')
                await asyncio.sleep(0.01)
                active -= 1
                return []
        client = TransitClient(Session(Delayed([]),Delayed([])),'test')
        await asyncio.gather(client.gps(),client.stops())

class FakeAPI:
    def __init__(self):
        from custom_components.warsaw_transit.models import Stop, Departure
        self.all_stops = [Stop('1','01','A',52,21), Stop('2','01','B',52.01,21), Stop('2','02','B',52.01,21)]
        self.tables = {('1','01'): [Departure('09:10:00','1','B','t'), Departure('09:30:00','2','B','t')], ('2','01'): [Departure('09:20:00','1','B','t'), Departure('09:40:00','2','B','t')], ('2','02'): [Departure('09:00:00','1','A','back'), Departure('09:20:00','2','A','back')]}
        self.vehicles = []
    async def stops(self): return self.all_stops
    async def lines(self, stop): return ['120']
    async def timetable(self, stop, line): return self.tables[(stop.group,stop.post)]
    async def gps(self): return self.vehicles

class EngineTests(unittest.IsolatedAsyncioTestCase):
    def routes(self):
        from custom_components.warsaw_transit.models import RouteConfig
        return [RouteConfig(name='home_to_work',start_stop='A',end_stop='B'), RouteConfig(name='work_to_home',start_stop='A',end_stop='B')]

    async def test_forward_post_and_next_two_json(self):
        from custom_components.warsaw_transit.engine import TransitEngine, WARSAW
        import json
        engine = TransitEngine(FakeAPI(), self.routes())
        await engine.async_setup()
        result = await engine.async_refresh(datetime(2026,11,6,9,0,tzinfo=WARSAW))
        r = result['home_to_work']
        self.assertEqual(r['end_post'],'01')
        self.assertEqual([d['departure'] for d in r['schedule']],['2026-11-06T09:10:00+01:00','2026-11-06T09:30:00+01:00'])
        self.assertEqual([d['departure_local'] for d in r['schedule']],['09:10','09:30'])
        self.assertIsNone(r['gps'])
        json.dumps(result)
        result = await engine.async_refresh(datetime(2026,11,6,9,20,tzinfo=WARSAW))
        self.assertEqual(len(result['home_to_work']['schedule']), 1)
        self.assertEqual(result['home_to_work']['schedule'][0]['departure'], '2026-11-06T09:30:00+01:00')

    async def test_timetables_cached_across_setup_refresh_ttl_and_date(self):
        from custom_components.warsaw_transit.engine import TransitEngine, WARSAW
        from datetime import timedelta
        from unittest.mock import patch
        api = FakeAPI()
        calls = []
        original = api.timetable
        async def counted(stop, line):
            calls.append((stop.group, stop.post, line))
            return await original(stop, line)
        api.timetable = counted
        engine = TransitEngine(api, self.routes())
        now = datetime.now(WARSAW)
        with patch('time.monotonic', return_value=1000):
            await engine.async_setup()
            setup_calls = len(calls)
            await engine.async_refresh(now)
            await engine.async_refresh(now)
            self.assertEqual(len(calls), setup_calls)
        with patch('time.monotonic', return_value=1901):
            await engine.async_refresh(now)
            self.assertEqual(len(calls), setup_calls + 2)
            await engine.async_refresh(now + timedelta(days=1))
            self.assertEqual(len(calls), setup_calls + 4)

    async def test_refresh_rejects_disappeared_forward_route(self):
        from custom_components.warsaw_transit.engine import TransitEngine, WARSAW
        from custom_components.warsaw_transit.models import ConfigurationError
        api = FakeAPI()
        engine = TransitEngine(api,self.routes())
        await engine.async_setup()
        api.tables[('2','01')] = []
        # Schedule changes become visible after the bounded timetable TTL.
        engine._tables = {key: (stamp - 901, rows) for key, (stamp, rows) in engine._tables.items()}
        with self.assertRaisesRegex(ConfigurationError,'forward'):
            await engine.async_refresh(datetime(2026,11,6,9,0,tzinfo=WARSAW))

    async def test_midnight_extended_hours_keep_service_date(self):
        from custom_components.warsaw_transit.engine import TransitEngine, WARSAW
        from custom_components.warsaw_transit.models import Departure
        api = FakeAPI()
        api.tables[('1','01')] = [Departure('24:10:00','1','B','t'),Departure('24:30:00','2','B','t')]
        api.tables[('2','01')] = [Departure('24:20:00','1','B','t'),Departure('24:40:00','2','B','t')]
        engine = TransitEngine(api,self.routes())
        await engine.async_setup()
        result = await engine.async_refresh(datetime(2026,11,7,0,0,tzinfo=WARSAW))
        self.assertEqual(result['home_to_work']['schedule'][0]['departure'],'2026-11-07T00:10:00+01:00')

    async def test_motion_yields_estimated_candidate_never_verified_direction(self):
        from custom_components.warsaw_transit.engine import TransitEngine, WARSAW
        from custom_components.warsaw_transit.models import Vehicle
        from datetime import timedelta
        now = datetime(2026,11,6,9,0,tzinfo=WARSAW)
        api = FakeAPI()
        engine = TransitEngine(api,self.routes())
        await engine.async_setup()
        api.vehicles = [Vehicle('120','1','V',51.995,21,now)]
        self.assertIsNone((await engine.async_refresh(now))['home_to_work']['gps'])
        api.vehicles = [Vehicle('120','1','V',51.996,21,now+timedelta(seconds=30))]
        route = (await engine.async_refresh(now+timedelta(seconds=30)))['home_to_work']
        self.assertIsNotNone(route['gps'], 'fresh schedule and motion must yield an estimated candidate')
        self.assertEqual(route['gps']['vehicle_id'], 'V')
        self.assertEqual(route['gps']['line'], '120')
        self.assertEqual(route['gps']['brigade'], '1')
        self.assertEqual(route['gps']['latitude'], 51.996)
        self.assertEqual(route['gps']['longitude'], 21)
        self.assertAlmostEqual(route['gps']['distance_km'], 0.44478, places=4)
        self.assertEqual(route['gps']['gps_updated'], (now+timedelta(seconds=30)).isoformat())
        self.assertEqual(route['gps']['direction'], 'approaching_candidate')
        self.assertEqual(route['gps']['certainty'], 'estimated_schedule_and_motion')
        self.assertIn('not guaranteed', route['gps']['warning'])
        self.assertEqual(route['status'], 'approaching_candidate')
        self.assertEqual(route['uncertainty_reason'], 'direction_estimated_without_trip_id')
        # Increasing distance rejects vehicles moving away, without an endpoint vector.
        api.vehicles = [Vehicle('120','1','V',52.006,21,now+timedelta(seconds=60))]
        self.assertIsNone((await engine.async_refresh(now+timedelta(seconds=60)))['home_to_work']['gps'])
        api.vehicles = [Vehicle('120','1','V',52.007,21,now+timedelta(seconds=90))]
        self.assertIsNone((await engine.async_refresh(now+timedelta(seconds=90)))['home_to_work']['gps'])
        # Stale snapshots, even valid movement, cannot confirm direction.
        api.vehicles = [Vehicle('120','1','V',51.997,21,now)]
        self.assertIsNone((await engine.async_refresh(now+timedelta(seconds=150)))['home_to_work']['gps'])

    async def test_candidate_bounds_closest_late_and_ambiguous_schedule(self):
        from custom_components.warsaw_transit.engine import TransitEngine, WARSAW
        from custom_components.warsaw_transit.models import Vehicle, Departure
        from datetime import timedelta
        now = datetime(2026,11,6,9,0,tzinfo=WARSAW)
        api = FakeAPI()
        engine = TransitEngine(api,self.routes())
        await engine.async_setup()
        async def sample(old, current, stamp=30, age=0):
            engine._previous = {v.vehicle_id:v for v in old}
            api.vehicles = current
            return (await engine.async_refresh(now+timedelta(seconds=stamp+age)))['home_to_work']
        old = Vehicle('120','1','V',51.995,21,now)
        good = Vehicle('120','1','V',51.996,21,now+timedelta(seconds=30))
        closer_old = Vehicle('120','2','C',51.997,21,now)
        closer = Vehicle('120','2','C',51.998,21,good.time)
        self.assertEqual((await sample([old,closer_old],[good,closer]))['gps']['vehicle_id'],'C')
        for v, stamp, age in [(good,30,121), (Vehicle('120','1','V',51.996,21,now),0,0),
                              (Vehicle('120','1','V',51.996,21,now+timedelta(seconds=4)),4,0),
                              (Vehicle('120','1','V',51.996,21,now+timedelta(seconds=121)),121,0),
                              (Vehicle('999','1','V',51.996,21,good.time),30,0),
                              (Vehicle('120','9','V',51.996,21,good.time),30,0),
                              (Vehicle('120','1','V',51.99501,21,good.time),30,0),
                              (Vehicle('120','1','V',52.006,21,good.time),30,0),
                              (Vehicle('120','1','V',51.996,21,good.time+timedelta(seconds=1)),30,0)]:
            with self.subTest(vehicle=v,stamp=stamp,age=age):
                result = await sample([old],[v],stamp,age)
                self.assertIsNone(result['gps'])
                self.assertEqual(result['status'],'unknown')
                self.assertTrue(result['uncertainty_reason'])
        fast_old = Vehicle('120','1','V',51.98,21,now)
        self.assertIsNone((await sample([fast_old],[good]))['gps'])
        far_old = Vehicle('120','1','V',51.89,21,now)
        far = Vehicle('120','1','V',51.891,21,good.time)
        self.assertIsNone((await sample([far_old],[far]))['gps'])
        # A safely moving bus may be late, but remains excluded from future schedule.
        late_now = now+timedelta(minutes=15)
        engine._previous = {'V':Vehicle('120','1','V',51.995,21,late_now)}
        api.vehicles = [Vehicle('120','1','V',51.996,21,late_now+timedelta(seconds=30))]
        late = (await engine.async_refresh(late_now+timedelta(seconds=30)))['home_to_work']
        self.assertIsNotNone(late['gps'])
        self.assertEqual(len(late['schedule']),1)
        # Outside either schedule window, even valid fresh motion is ineligible.
        for offset in (-60, 21):
            outside_now = now+timedelta(minutes=offset)
            engine._previous = {'V':Vehicle('120','1','V',51.995,21,outside_now)}
            api.vehicles = [Vehicle('120','1','V',51.996,21,outside_now+timedelta(seconds=30))]
            self.assertIsNone((await engine.async_refresh(outside_now+timedelta(seconds=30)))['home_to_work']['gps'])
        # A second matching forward departure makes this brigade ambiguous.
        api.tables[('1','01')].append(Departure('09:25:00','1','B','t'))
        api.tables[('2','01')].append(Departure('09:28:00','1','B','t'))
        engine._tables.clear()
        self.assertIsNone((await sample([old],[good]))['gps'])

    async def test_configuration_ambiguity_and_no_forward(self):
        from custom_components.warsaw_transit.engine import TransitEngine
        from custom_components.warsaw_transit.models import ConfigurationError
        api = FakeAPI()
        api.tables[('2','02')] = api.tables[('2','01')]
        with self.assertRaisesRegex(ConfigurationError, 'Ambiguous'):
            await TransitEngine(api,self.routes()).async_setup()
        api.tables[('2','01')] = []
        api.tables[('2','02')] = []
        with self.assertRaisesRegex(ConfigurationError, 'forward'):
            await TransitEngine(api,self.routes()).async_setup()
        with self.assertRaisesRegex(ConfigurationError, 'two'):
            await TransitEngine(api,self.routes()[:1]).async_setup()

class CoreTests(unittest.TestCase):
    def test_clock_and_stop_resolution(self):
        from custom_components.warsaw_transit.engine import schedule_datetime, resolve_stops
        from custom_components.warsaw_transit.models import Stop, ConfigurationError
        self.assertEqual(schedule_datetime('25:10:00', date(2026, 11, 6)).isoformat(), '2026-11-07T01:10:00+01:00')
        stops = [Stop('1','01','A',52,21), Stop('1','02','A',52,21)]
        self.assertEqual(len(resolve_stops(stops, ' a ')), 2)
        self.assertEqual(resolve_stops(stops, 'A', '02')[0].post, '02')
        with self.assertRaises(ConfigurationError): resolve_stops(stops, 'Missing')
        with self.assertRaises(ValueError): schedule_datetime('12:99:00', date(2026,11,6))
        with self.assertRaises(ValueError): schedule_datetime('02:30:00', date(2026,3,29))

    def test_route_pydantic_boundary(self):
        from custom_components.warsaw_transit.models import RouteConfig
        from pydantic import ValidationError
        route = RouteConfig.model_validate({'name':'home_to_work','start_stop':' A ','end_stop':'B'})
        self.assertEqual(route.start_stop, 'A')
        with self.assertRaises(ValidationError): RouteConfig.model_validate({'name':'a','start_stop':'A','end_stop':'B','line':'120'})
        with self.assertRaises(ValidationError): RouteConfig.model_validate({'name':'a','start_stop':42,'end_stop':'B'})

    def test_route_slug_posts_and_model_validator(self):
        from custom_components.warsaw_transit.models import RouteConfig
        from pydantic import ValidationError
        valid = dict(name='home_to_work', start_stop='A', end_stop='B')
        for name in ('Home', 'a-b', '1route', 'a' * 65):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                RouteConfig.model_validate(dict(valid, name=name))
        for post in ('1', '001', 'ab', ''):
            with self.subTest(post=post), self.assertRaises(ValidationError):
                RouteConfig.model_validate(dict(valid, start_post=post))
        self.assertEqual(RouteConfig.model_validate(dict(valid, start_post='01')).start_post, '01')
        with self.assertRaises(ValidationError):
            RouteConfig.model_validate(dict(valid, end_stop=' a '))
        self.assertEqual(RouteConfig.model_json_schema()['properties']['name']['pattern'], '^[a-z][a-z0-9_]{0,63}$')
        with self.assertRaises(ValidationError):
            RouteConfig.model_validate(dict(valid, start_stop=' '))

    def test_models_validate_distinct_route_and_coordinates(self):
        from custom_components.warsaw_transit.models import Stop, RouteConfig, ConfigurationError
        self.assertEqual(Stop('1','01','A',52,21).post, '01')
        with self.assertRaises(ValueError): RouteConfig('a','A','A')
        with self.assertRaises(ValueError): Stop('1','01','A',200,21)
