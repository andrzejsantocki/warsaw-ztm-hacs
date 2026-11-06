"""Real HA coordinator with upstream transport isolated."""
from unittest.mock import AsyncMock, patch
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from types import SimpleNamespace


@pytest.mark.asyncio
async def test_coordinator_initializes_once_and_maps_auth(tmp_path):
    from custom_components.warsaw_transit.coordinator import TransitCoordinator
    from custom_components.warsaw_transit.api import AuthError, TransitError
    hass = HomeAssistant(str(tmp_path))
    entry = SimpleNamespace(data={"api_key": "not-a-real-key", "routes": [{"name": "a", "start_stop": "A", "end_stop": "B"}, {"name": "b", "start_stop": "B", "end_stop": "A"}]})
    engine = SimpleNamespace(async_setup=AsyncMock(), async_refresh=AsyncMock(return_value={"a": {"schedule": [], "gps": None, "status": "unknown"}}))
    with patch("custom_components.warsaw_transit.coordinator.TransitEngine", return_value=engine), patch("custom_components.warsaw_transit.coordinator.async_get_clientsession"):
        coordinator = TransitCoordinator(hass, entry)
        assert coordinator.update_interval.total_seconds() == 20
        assert "a" in await coordinator._async_update_data()
        await coordinator._async_update_data()
        engine.async_setup.assert_awaited_once()
        engine.async_refresh.side_effect = AuthError("Invalid key")
        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()
        engine.async_refresh.side_effect = TransitError("Unavailable")
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()
    await hass.async_stop()


@pytest.mark.asyncio
async def test_last_error_records_redacted_traceback_and_sensor(tmp_path):
    from custom_components.warsaw_transit.coordinator import TransitCoordinator
    from custom_components.warsaw_transit.api import TransitError
    from custom_components.warsaw_transit.sensor import LastErrorSensor
    hass = HomeAssistant(str(tmp_path))
    entry = SimpleNamespace(data={"api_key": "sekret-key", "routes": [{"name": "a", "start_stop": "A", "end_stop": "B"}, {"name": "b", "start_stop": "B", "end_stop": "A"}]})
    engine = SimpleNamespace(async_setup=AsyncMock(), async_refresh=AsyncMock(return_value={"a": {}}))
    with patch("custom_components.warsaw_transit.coordinator.TransitEngine", return_value=engine), patch("custom_components.warsaw_transit.coordinator.async_get_clientsession"):
        coordinator = TransitCoordinator(hass, entry)
        sensor = LastErrorSensor(coordinator, "entry")
        await coordinator._async_update_data()
        assert sensor.native_value is None and sensor.extra_state_attributes["error_count"] == 0
        engine.async_refresh.side_effect = TransitError("upstream said sekret-key is slow")
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()
        engine.async_refresh.side_effect = RuntimeError("unexpected boom")
        with pytest.raises(RuntimeError):
            await coordinator._async_update_data()
    attrs = sensor.extra_state_attributes
    assert sensor.native_value.tzinfo is not None and sensor.available
    assert attrs["error_count"] == 2
    assert attrs["error_type"] == "RuntimeError" and attrs["message"] == "unexpected boom"
    assert "Traceback" in attrs["traceback"] and "unexpected boom" in attrs["traceback"]
    assert "traceback" in sensor._unrecorded_attributes
    engine.async_refresh.side_effect = TransitError("sekret-key leaked")
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    tb = sensor.extra_state_attributes["traceback"]
    assert "sekret-key" not in tb and "[redacted]" in tb and "TransitError" in tb
    await hass.async_stop()
