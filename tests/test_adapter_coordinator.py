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
