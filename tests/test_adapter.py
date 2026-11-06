"""Adapter checks against real Home Assistant, not module stubs."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from custom_components.warsaw_transit import TransitView


def test_route_form_one_to_four_journeys_and_validation():
    from custom_components.warsaw_transit.config_flow import route_schema, route_data
    import voluptuous as vol
    schema = route_schema()
    raw = {"api_key": "secret", "route_1_name": "home_to_work", "route_1_start_stop": "A", "route_1_end_stop": "B", "route_2_name": "work_to_home", "route_2_start_stop": "B", "route_2_end_stop": "A"}
    assert len(route_data(schema(raw))["routes"]) == 2
    # Route 2's prefilled name alone does not make the slot used.
    one = {"api_key": "secret", "route_1_name": "solo", "route_1_start_stop": "A", "route_1_end_stop": "B", "route_2_name": "work_to_home"}
    assert [r["name"] for r in route_data(schema(one))["routes"]] == ["solo"]
    three = dict(raw, route_3_start_stop="C", route_3_end_stop="D")
    assert [r["name"] for r in route_data(schema(three))["routes"]] == ["home_to_work", "work_to_home", "route_3"]
    with pytest.raises(vol.Invalid, match="Journey 4"):
        route_data(schema(dict(raw, route_4_start_stop="C")))
    raw["route_2_name"] = "home_to_work"
    with pytest.raises(vol.Invalid):
        route_data(schema(raw))


def test_sensor_timestamp_and_vehicle_attributes():
    from custom_components.warsaw_transit.sensor import TransitSensor
    coordinator = SimpleNamespace(data={"home_to_work": {"schedule": [{"departure": "2026-11-06T12:00:00+01:00"}], "gps": {"vehicle_id": "123", "distance_km": 0.8}, "status": "approaching", "error": None}}, last_update_success=True)
    first = TransitSensor(coordinator, "entry", "home_to_work", "next_1")
    assert isinstance(first.native_value, datetime)
    assert first.native_value.utcoffset() is not None
    assert TransitSensor(coordinator, "entry", "home_to_work", "next_2").native_value is None
    bus = TransitSensor(coordinator, "entry", "home_to_work", "approaching_bus")
    assert bus.native_value == "123"
    assert bus.extra_state_attributes["distance_km"] == 0.8
    assert bus.extra_state_attributes["status"] == "approaching"


def test_line_sensor_prefers_gps_then_schedule():
    from custom_components.warsaw_transit.sensor import TransitSensor
    route = {"schedule": [{"departure": "2026-11-06T12:00:00+01:00", "line": "120", "brigade": "6", "headsign": "Olesin"}],
             "gps": {"vehicle_id": "5428", "line": "120", "brigade": "6"}, "status": "approaching_candidate", "error": None}
    coordinator = SimpleNamespace(data={"home_to_work": route}, last_update_success=True)
    line = TransitSensor(coordinator, "entry", "home_to_work", "line")
    assert line.native_value == "120"
    assert line.extra_state_attributes["source"] == "gps"
    assert line.extra_state_attributes["vehicle_id"] == "5428"
    route["gps"] = None
    assert line.native_value == "120"
    assert line.extra_state_attributes["source"] == "schedule"
    assert line.extra_state_attributes["vehicle_id"] is None
    assert line.extra_state_attributes["headsign"] == "Olesin"
    route["schedule"] = []
    assert line.native_value is None
    coordinator.last_update_success = False
    assert line.native_value is None


@pytest.mark.asyncio
async def test_http_is_authenticated_and_only_reads_cache():
    from aiohttp import web
    coordinator = SimpleNamespace(data={"home_to_work": {"schedule": [{"departure": "later"}], "gps": None, "status": "unknown", "error": None}}, last_update_success=True, async_refresh=AsyncMock())
    hass = SimpleNamespace(data={"warsaw_transit": {"entry": coordinator}})
    request = SimpleNamespace(app={"hass": hass})
    for resource in ("schedule", "gps"):
        view = TransitView(resource)
        assert view.requires_auth
        response = await view.get(request, "entry", "home_to_work")
        assert response.status == 200
    coordinator.async_refresh.assert_not_called()
    with pytest.raises(web.HTTPNotFound):
        await TransitView("gps").get(request, "entry", "missing")
