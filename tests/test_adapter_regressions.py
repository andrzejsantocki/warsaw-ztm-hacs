"""Reviewed adapter regressions, exercised against real Home Assistant."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError

from custom_components.warsaw_transit import TransitView
from custom_components.warsaw_transit.sensor import TransitSensor


def cached(success=True):
    return SimpleNamespace(data={"a": {"schedule": [{"departure": "2026-11-06T12:00:00+01:00"}], "gps": {"vehicle_id": "123", "distance_km": 0.8}, "status": "approaching", "error": None}}, last_update_success=success, last_success="2026-11-06T10:00:00+00:00", last_exception=RuntimeError("offline"), async_refresh=AsyncMock())


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["gps", "schedule"])
async def test_failed_refresh_never_serves_current_cache(resource):
    coordinator = cached(False)
    request = SimpleNamespace(app={"hass": SimpleNamespace(data={"warsaw_transit": {"entry": coordinator}})})
    response = await TransitView(resource).get(request, "entry", "a")
    body = json.loads(response.body)
    assert response.status == 503
    assert body["status"] == "unavailable"
    assert body[resource] is None
    assert body["fresh"] is False
    assert body["last_success"] == coordinator.last_success
    coordinator.async_refresh.assert_not_called()


@pytest.mark.parametrize("kind", ["next_1", "next_2", "approaching_bus"])
def test_stale_sensor_has_no_current_value_or_details(kind):
    sensor = TransitSensor(cached(False), "entry", "a", kind)
    assert sensor.native_value is None
    attributes = sensor.extra_state_attributes
    assert attributes["status"] == "unavailable"
    assert attributes["fresh"] is False
    assert "vehicle_id" not in attributes
    assert "departure" not in attributes
    assert attributes["last_success"] == "2026-11-06T10:00:00+00:00"


@pytest.mark.parametrize("kind", ["next_1", "next_2"])
def test_null_schedule_is_unknown(kind):
    coordinator = cached()
    coordinator.data["a"]["schedule"] = None
    sensor = TransitSensor(coordinator, "entry", "a", kind)
    assert sensor.native_value is None
    assert "departure" not in sensor.extra_state_attributes


@pytest.mark.asyncio
async def test_configuration_failure_latches_until_new_coordinator(tmp_path):
    from custom_components.warsaw_transit.api import ConfigurationError
    from custom_components.warsaw_transit.coordinator import TransitCoordinator
    hass = HomeAssistant(str(tmp_path))
    entry = SimpleNamespace(data={"api_key": "secret", "routes": [{"name": "a", "start_stop": "A", "end_stop": "B"}]})
    detail = "Unknown start post 99 for A. Available posts: 01, 02"
    engine = SimpleNamespace(async_setup=AsyncMock(side_effect=ConfigurationError(detail)), async_refresh=AsyncMock())
    try:
        with patch("custom_components.warsaw_transit.coordinator.TransitEngine", return_value=engine), patch("custom_components.warsaw_transit.coordinator.async_get_clientsession"):
            coordinator = TransitCoordinator(hass, entry)
            for _ in range(3):
                with pytest.raises(ConfigEntryError, match=detail):
                    await coordinator._async_update_data()
            assert coordinator.update_interval is None
            with pytest.raises(ConfigEntryError, match=detail):
                await coordinator._async_refresh(raise_on_entry_error=True)
            assert coordinator.last_update_success is False
            assert str(coordinator.last_exception) == detail
            engine.async_setup.assert_awaited_once()
            engine.async_refresh.assert_not_awaited()
            replacement = TransitCoordinator(hass, entry)
            engine.async_setup.side_effect = None
            engine.async_refresh.return_value = {}
            assert await replacement._async_update_data() == {}
            assert replacement.last_success is not None
    finally:
        await hass.async_stop()


def form_input():
    return {"api_key": "sensitive-key", "route_1_name": "a", "route_1_start_stop": "A", "route_1_end_stop": "B", "route_1_start_post": "99", "route_2_name": "b", "route_2_start_stop": "B", "route_2_end_stop": "A"}


@pytest.mark.asyncio
async def test_flow_configuration_detail_and_input_preserved():
    from custom_components.warsaw_transit.api import ConfigurationError
    from custom_components.warsaw_transit.config_flow import WarsawTransitConfigFlow
    flow = WarsawTransitConfigFlow()
    flow.hass = SimpleNamespace()
    raw = form_input()
    detail = "Unknown start post 99 for A. Available posts: 01, 02"
    with patch("custom_components.warsaw_transit.config_flow.async_get_clientsession"), patch("custom_components.warsaw_transit.engine.TransitEngine.async_setup", side_effect=ConfigurationError(detail)):
        result = await flow.async_step_user(raw)
    assert result["errors"] == {"base": "invalid_stops"}
    assert result["description_placeholders"]["detail"] == detail
    defaults = {str(key): key.default() for key in result["data_schema"].schema}
    assert defaults["api_key"] == ""
    assert all(defaults[key] == value for key, value in raw.items() if key != "api_key")


@pytest.mark.asyncio
async def test_flow_pydantic_error_is_actionable_and_redacted():
    from pydantic import BaseModel, ValidationError
    from custom_components.warsaw_transit.config_flow import WarsawTransitConfigFlow
    class InvalidRoute(BaseModel):
        start_post: int
    try:
        InvalidRoute(start_post="sensitive-key")
    except ValidationError as err:
        failure = err
    flow = WarsawTransitConfigFlow()
    flow.hass = SimpleNamespace()
    with patch("custom_components.warsaw_transit.models.RouteConfig", side_effect=failure), patch("custom_components.warsaw_transit.config_flow.async_get_clientsession"):
        result = await flow.async_step_user(form_input())
    assert result["errors"] == {"base": "invalid_routes"}
    detail = result["description_placeholders"]["detail"]
    assert "start_post" in detail
    assert "sensitive-key" not in str(result)


@pytest.mark.asyncio
async def test_flow_retries_reuse_client_and_changed_key_replaces_it():
    from custom_components.warsaw_transit.api import TransitError, TransitClient
    from custom_components.warsaw_transit.config_flow import WarsawTransitConfigFlow
    flow = WarsawTransitConfigFlow()
    flow.hass = SimpleNamespace()
    clients = []
    async def failed_setup(engine):
        clients.append(engine.client)
        raise TransitError("offline")
    with patch("custom_components.warsaw_transit.config_flow.async_get_clientsession"), patch("custom_components.warsaw_transit.engine.TransitEngine.async_setup", failed_setup):
        raw = form_input()
        await flow.async_step_user(raw)
        await flow.async_step_user(raw)
        await flow.async_step_user({**raw, "api_key": "different-key"})
    assert isinstance(clients[0], TransitClient)
    assert clients[0] is clients[1]
    assert clients[2] is not clients[1]


@pytest.mark.asyncio
async def test_flow_real_client_cooldown_prevents_repeated_transport():
    from custom_components.warsaw_transit.config_flow import WarsawTransitConfigFlow
    from unittest.mock import Mock
    from aiohttp import ClientConnectionError
    flow = WarsawTransitConfigFlow()
    flow.hass = SimpleNamespace()
    session = Mock()
    session.post.side_effect = ClientConnectionError("offline")
    with patch("custom_components.warsaw_transit.config_flow.async_get_clientsession", return_value=session):
        assert (await flow.async_step_user(form_input()))["errors"] == {"base": "cannot_connect"}
        assert (await flow.async_step_user(form_input()))["errors"] == {"base": "cooldown"}
        assert session.post.call_count == 1
        await flow.async_step_user({**form_input(), "api_key": "new-key"})
        assert session.post.call_count == 2


@pytest.mark.asyncio
async def test_registered_endpoints_with_real_ha_auth_server(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer
    from homeassistant.auth import auth_manager_from_config
    from homeassistant.components.http import HomeAssistantHTTP
    hass = HomeAssistant(str(tmp_path))
    hass.auth = await auth_manager_from_config(hass, [], [])
    http = HomeAssistantHTTP(hass, None, None, None, ["127.0.0.1"], 0, [], "modern")
    hass.http = http
    coordinator = cached()
    hass.data["warsaw_transit"] = {"entry": coordinator}
    client = None
    try:
        await http.async_initialize(cors_origins=[], use_x_forwarded_for=False, login_threshold=-1, is_ban_enabled=False, use_x_frame_options=True)
        for resource in ("gps", "schedule"):
            http.register_view(TransitView(resource))
        user = await hass.auth.async_create_user("Adapter test")
        refresh = await hass.auth.async_create_refresh_token(user, client_id="http://adapter-test.local/")
        token = hass.auth.async_create_access_token(refresh)
        client = TestClient(TestServer(http.app))
        await client.start_server()
        for resource in ("gps", "schedule"):
            url = f"/api/warsaw_transit/entry/a/{resource}"
            assert (await client.get(url)).status == 401
            assert (await client.get(url, headers={"Authorization": "Bearer invalid"})).status == 401
            response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            assert response.status == 200
            assert (await response.json())["fresh"] is True
        coordinator.last_update_success = False
        for resource in ("gps", "schedule"):
            response = await client.get(f"/api/warsaw_transit/entry/a/{resource}", headers={"Authorization": f"Bearer {token}"})
            assert response.status == 503
            assert (await response.json())[resource] is None
        assert (await client.get("/api/warsaw_transit/entry/missing/gps", headers={"Authorization": f"Bearer {token}"})).status == 404
        coordinator.async_refresh.assert_not_called()
    finally:
        if client is not None:
            await client.close()
        await hass.async_stop()
