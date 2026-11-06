# Warsaw ZTM Bus Commute

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![GitHub release](https://img.shields.io/github/v/release/andrzejsantocki/warsaw-ztm-hacs)](https://github.com/andrzejsantocki/warsaw-ztm-hacs/releases)
[![Validate](https://github.com/andrzejsantocki/warsaw-ztm-hacs/actions/workflows/validate.yml/badge.svg)](https://github.com/andrzejsantocki/warsaw-ztm-hacs/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Home Assistant integration for your daily Warsaw bus journeys. It shows the next timetable departures and the live position of the bus that is approaching your stop, using the official [Warsaw Open Data](https://dane.um.warszawa.pl) API.

🇵🇱 [Wersja polska ↓](#po-polsku)

## Features

- **1 to 4 journeys**, e.g. home → work and work → home.
- **Automatic line discovery.** Enter two stops; the integration finds every direct bus line that runs between them in the right direction.
- **Next two departures** per journey, as timestamps and as plain Warsaw time.
- **Approaching bus** from live GPS: vehicle number, line, position and distance to your stop.
- **Voice-assistant friendly** text sensor with departures in local time.
- **Polish time handled correctly**, including summer/winter time changes (`Europe/Warsaw`).
- **Gentle on the API**: one shared poll every 20 s, timetables cached for 15 min, automatic back-off on errors.

## Requirements

- Home Assistant 2025.1 or newer (2026.3+ shows the integration icon).
- A free Warsaw Open Data API key: [dane.um.warszawa.pl → API key](https://dane.um.warszawa.pl/en/key-api).

## Installation

### HACS (recommended)

1. In HACS, open **⋮ → Custom repositories**, add `https://github.com/andrzejsantocki/warsaw-ztm-hacs` with type **Integration**.
2. Search for **Warsaw ZTM Bus Commute** and download it.
3. Restart Home Assistant.

### Manual

Copy `custom_components/warsaw_transit` into `/config/custom_components/` and restart Home Assistant.

## Configuration

Go to **Settings → Devices & services → Add integration → Warsaw ZTM Bus Commute** and fill in:

| Field | Required | Description |
|---|---|---|
| API key | yes | Your Warsaw Open Data key. |
| Name | no | Unique ID for the journey: lowercase letters, digits, `_`. Defaults to `home_to_work`, `work_to_home`, `route_3`, `route_4`. |
| Start stop / Destination stop | yes | Exact stop names as used by ZTM, e.g. `Dworzec Wileński`. |
| Start post / Destination post | no | Two-digit post (słupek) number, e.g. `01`. Needed only when a stop has several matching posts; the form tells you which ones. |

Journey 1 is required. Journeys 2–4 are optional; leave both stop fields empty to skip one. Change journeys later with **⋮ → Reconfigure** on the integration.

Example:

| Journey | From | To |
|---|---|---|
| `home_to_work` | Dworzec Wileński, post 01 | Centrum, post 02 |
| `work_to_home` | Centrum, post 03 | Dworzec Wileński, post 02 |

## Entities

For every journey (`<journey>` is its name):

| Entity | State | Useful attributes |
|---|---|---|
| `sensor.warsaw_transit_<journey>_next_1` | Next departure (timestamp) | `departure_local`, `line`, `brigade`, `headsign` |
| `sensor.warsaw_transit_<journey>_next_2` | Departure after that (timestamp) | same as above |
| `sensor.warsaw_transit_<journey>_departures` | Both times as text, e.g. `13:39, 13:59` | `lines`, `headsigns` |
| `sensor.warsaw_transit_<journey>_approaching_bus` | Vehicle number of the approaching bus | `line`, `latitude`, `longitude`, `distance_km`, `gps_updated` |
| `sensor.warsaw_transit_<journey>_line` | Line of the approaching bus, or of the next departure | `source` (`gps` / `schedule`), `vehicle_id` |

Plus one diagnostic sensor, `sensor.warsaw_transit_last_error`, with the time and details of the latest failed update.

> **Times in Developer Tools look one or two hours off?** Home Assistant always stores timestamp states in UTC (`12:39:00+00:00` = 13:39 in Warsaw). Dashboards convert them automatically. In templates, use `departure_local` or `| as_local`.

### Voice assistants and LLM agents

Expose the `…_departures` sensors to Assist or your conversation agent, **not** `next_1`/`next_2`. Agents tend to read UTC timestamp states as local time and give wrong answers.

### Dashboard example

```yaml
type: entities
title: Bus to work
entities:
  - entity: sensor.warsaw_transit_home_to_work_next_1
    name: Next bus
    format: relative
  - entity: sensor.warsaw_transit_home_to_work_next_2
    name: Then
    format: time
  - entity: sensor.warsaw_transit_home_to_work_line
    name: Line
  - type: attribute
    entity: sensor.warsaw_transit_home_to_work_approaching_bus
    attribute: distance_km
    name: Approaching bus (km)
```

## How it works and limitations

- Departure times are **timetable times**, not live arrival predictions.
- The approaching bus is an **estimate**: a vehicle is picked when its line and brigade match an upcoming departure and it is moving towards your stop. The public feed has no trip IDs, so direction cannot be guaranteed. An `unknown` state means no confident match, not that no bus is coming.
- Only **direct bus journeys** are supported (no trams, transfers or walking legs).
- Times around midnight and the night of the October clock change rely on heuristics, because the API has no service-date parameter.

Full details: [CORE_LIMITATIONS.md](CORE_LIMITATIONS.md).

## Local REST API

For bots and scripts, the integration serves its cached data (it never triggers extra Warsaw API calls). Authenticate with a Home Assistant long-lived access token:

```bash
curl -H "Authorization: Bearer $HA_TOKEN" \
  "$HA_URL/api/warsaw_transit/$ENTRY_ID/home_to_work/schedule"   # or /gps
```

`ENTRY_ID` is shown in the integration's URL under Settings → Devices & services.

## Troubleshooting

| Problem | Fix |
|---|---|
| "Ambiguous origin/destination" | Set the post number the message suggests. |
| "No forward shared routes" | No direct bus runs between those stops in that direction today. Check names and posts. |
| API key rejected | Generate a new key and use **Reconfigure**. |
| Sensors `unavailable` | See the attributes of `sensor.warsaw_transit_last_error`. |

## Development

```bash
python -m pytest tests -q -o asyncio_default_fixture_loop_scope=function
```

Tests run against real Home Assistant modules with synthetic API fixtures. `live_check.py` queries the live API using a key from a local `.env` (never committed). Hassfest and HACS validation run on every push.

## License

[MIT](LICENSE). Data © Miasto Stołeczne Warszawa, [Warsaw Open Data](https://dane.um.warszawa.pl). This project is not affiliated with ZTM Warszawa.

---

## Po polsku

Integracja Home Assistant dla codziennych dojazdów autobusami ZTM w Warszawie: najbliższe odjazdy z rozkładu oraz autobus, który właśnie zbliża się do Twojego przystanku (GPS). Dane pochodzą z oficjalnego API [Otwarte dane po warszawsku](https://dane.um.warszawa.pl).

### Funkcje

- Od 1 do 4 tras, np. dom → praca i praca → dom.
- Linie wykrywane automatycznie: wystarczy podać dwa przystanki.
- Dwa najbliższe odjazdy dla każdej trasy.
- Zbliżający się autobus z GPS: numer pojazdu, linia, położenie i odległość.
- Poprawny czas polski, również przy zmianie czasu letniego i zimowego.

### Instalacja

1. HACS → **⋮ → Niestandardowe repozytoria** → dodaj `https://github.com/andrzejsantocki/warsaw-ztm-hacs`, typ **Integration**.
2. Wyszukaj **Warsaw ZTM Bus Commute** i pobierz.
3. Zrestartuj Home Assistant.
4. **Ustawienia → Urządzenia i usługi → Dodaj integrację → Warsaw ZTM Bus Commute.**

Potrzebny jest bezpłatny klucz API ze strony [dane.um.warszawa.pl](https://dane.um.warszawa.pl/en/key-api).

### Konfiguracja

- **Nazwa**: unikalna nazwa trasy (małe litery, cyfry, `_`), np. `dom_praca`.
- **Przystanek początkowy i docelowy**: dokładne nazwy jak w ZTM.
- **Słupek** (opcjonalnie): dwucyfrowy numer, np. `01`. Potrzebny tylko, gdy przystanek ma kilka pasujących słupków; formularz podpowie które.

Trasa 1 jest wymagana, trasy 2–4 są opcjonalne (zostaw oba przystanki puste, aby pominąć trasę).

### Czujniki

| Czujnik | Stan |
|---|---|
| `…_next_1`, `…_next_2` | Najbliższe odjazdy (znacznik czasu; atrybut `departure_local`, np. `13:39`) |
| `…_departures` | Obie godziny jako tekst, np. `13:39, 13:59` |
| `…_approaching_bus` | Numer zbliżającego się pojazdu |
| `…_line` | Linia zbliżającego się autobusu lub najbliższego odjazdu |

W Narzędziach deweloperskich znaczniki czasu są pokazane w UTC (tak zapisuje je Home Assistant). Na kartach wyświetlają się w czasie lokalnym. **Asystentom głosowym i LLM udostępniaj czujnik `…_departures`**, a nie `next_1`/`next_2`.

### Ograniczenia

Godziny odjazdu pochodzą z rozkładu, to nie jest prognoza przyjazdu. Kierunek jazdy z GPS jest szacowany, nie gwarantowany. Obsługiwane są tylko bezpośrednie połączenia autobusowe.
