# Warsaw Transit Routes

Home Assistant custom integration with two configurable, named bus journeys. No bus line or vehicle is hardcoded. Discover lines shared by the configured stop posts and use forward timetable matches.

## Status

Version 0.1.1 development build, installable via HACS as a custom repository. Verified against Home Assistant 2025.1.4 / Python 3.12: 42 tests and 17 subtests passed (9 third-party warnings). Real HA HTTP authentication tests cover 401, 200, 404 and stale-cache 503 responses. Live Warsaw API checks returned both routes' schedules and estimated GPS candidates. GPS direction remains heuristic, not guaranteed.

## Installation

### HACS (recommended)

1. HACS → ⋮ → Custom repositories → add `https://github.com/andrzejsantocki/warsaw-ztm-hacs`, type **Integration**.
2. Search for **Warsaw Transit Routes** in HACS and download it.
3. Restart Home Assistant, then Settings → Devices & services → Add integration → Warsaw Transit.

### Manual

Copy `custom_components/warsaw_transit` into your HA `/config/custom_components/` directory, restart HA, then add the integration as above.

## Configuration

Generate a raw JWT at https://dane.um.warszawa.pl/en/key-api. Enter it in the integration's password field. Set exactly two journeys, each with:

- `name`: unique machine-friendly name, defaults `home_to_work` / `work_to_home`.
- `start_stop`: exact Warsaw stop name.
- `end_stop`: exact destination stop name.
- `start_post`, `end_post`: optional two-digit stop post numbers. Required when a name resolves to multiple compatible posts. Separate journeys may use different posts for the same stop group.

Example configuration (not embedded in the integration logic):

- `home_to_work`: Dworzec Wileński **01** → Centrum **02**.
- `work_to_home`: Centrum **03** → Dworzec Wileński **02**.

Both metro stop names have multiple posts. The integration reports ambiguous names rather than silently choosing one. Only direct bus journeys are supported; no walking legs or transfers.

## Sensors

Each route creates two scheduled-departure timestamp sensors (`next_1`, `next_2`) and an approaching-vehicle sensor. GPS attributes include vehicle ID, line, brigade, latitude, longitude, last report time and `distance_km`.

Distance is haversine great-circle distance from bus to origin stop. No maps, geocoding service or routing dependency is used. Timetable departure is **not** a GPS ETA.

GPS provides an **estimated approaching candidate, never verified journey direction**. It chooses the closest straight-line-distance vehicle with one matching line/brigade forward timetable departure in the next 30 minutes (or up to 10 minutes late), two fresh observations 5–120 seconds apart, at least 15 metres decreasing distance to the boarding stop, plausible movement below 130 km/h, and distance below 10 km. Multiple eligible trips for the same line/brigade are rejected. No endpoint-vector corridor is assumed.

Candidate results have `status` and GPS `direction` set to `approaching_candidate`, `certainty: estimated_schedule_and_motion`, `uncertainty_reason: direction_estimated_without_trip_id`, and an explicit GPS warning. **Direction is not guaranteed without a trip feed**: an opposite-direction or already-passed bus can move closer again on a curved route. Increasing distance rejects the current candidate, not every possible previously passed journey. No candidate yields `gps: null`, `status: unknown`, and `uncertainty_reason: no_unambiguous_fresh_schedule_and_motion_candidate`; it does **not** mean no bus is coming. Sensor attributes expose uncertainty; failed refreshes expose `update_failed` rather than stale candidate details. See [core limitations](CORE_LIMITATIONS.md).

Timetables are shared between setup and refreshes, cached for 15 minutes and invalidated when the Warsaw calendar date changes. One available future departure is retained; two is the maximum. Partial stop/GPS feeds skip invalid rows with warning counts; wholly unusable nonempty feeds fail safely.

## Separate bot APIs

Use the Home Assistant base URL, config entry ID and configured route name:

```
GET /api/warsaw_transit/ENTRY_ID/home_to_work/schedule
GET /api/warsaw_transit/ENTRY_ID/home_to_work/gps
```

Authenticate with the bot's **Home Assistant access token**, not the Warsaw key:

```bash
curl -H "Authorization: Bearer $HA_TOKEN" \
  "$HA_URL/api/warsaw_transit/$ENTRY_ID/home_to_work/schedule"
```

These resources return the coordinator's cached results and never trigger upstream requests. The config entry ID is visible in HA integration URLs and in the integration's sensor unique IDs. Route names must match your configuration.

## Errors and rate control

A single coordinator shares GPS polling across the route sensors. Requests are serialized; failures activate exponential cooldown, and HTTP Retry-After is honored. Authorization errors require reauthentication. Configuration errors identify the incorrect or ambiguous stop/post rather than hammering the API.

## Key storage

The supplied development key is stored only in local `.env`, mode 0600, excluded by `.gitignore`. `.env.example` contains no credential. Never publish or distribute `.env`.

Home Assistant does **not** automatically load this project's `.env`: enter the key in its config flow. HA stores integration credentials in its own configuration storage; protect backups accordingly.

## Standalone live check

```
.venv/bin/python live_check.py routes.example.json
```

It reads the private `.env` and requests two GPS samples at least 20 seconds apart. `inspect_route.py` is a development utility for inspecting post candidates. It is not required by HA.

## Tests

```
.venv/bin/python -m unittest discover -s tests -p test_core.py -v
.ha312-venv/bin/python -m pytest tests -q -o asyncio_default_fixture_loop_scope=function
```

No live API responses are fabricated. Unit tests use synthetic fixtures; live-check output comes from the actual Warsaw endpoint.
