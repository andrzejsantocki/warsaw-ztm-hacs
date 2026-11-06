"""Setup/unload behavior using real HA adapter APIs, fake transport."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import pytest
from custom_components.warsaw_transit import async_setup_entry, async_unload_entry


@pytest.mark.asyncio
async def test_views_registered_once_and_unload_shuts_down():
    coordinator = SimpleNamespace(async_config_entry_first_refresh=AsyncMock(), async_shutdown=AsyncMock())
    hass = SimpleNamespace(data={}, http=SimpleNamespace(register_view=Mock()), config_entries=SimpleNamespace(async_forward_entry_setups=AsyncMock(), async_unload_platforms=AsyncMock(return_value=True)))
    entry = SimpleNamespace(entry_id="entry", data={}, async_on_unload=Mock(), add_update_listener=Mock())
    with patch("custom_components.warsaw_transit.coordinator.TransitCoordinator", return_value=coordinator):
        assert await async_setup_entry(hass, entry)
        assert await async_setup_entry(hass, SimpleNamespace(entry_id="second", data={}, async_on_unload=Mock(), add_update_listener=Mock()))
    assert hass.http.register_view.call_count == 2
    assert await async_unload_entry(hass, entry)
    coordinator.async_shutdown.assert_awaited_once()
    assert "entry" not in hass.data["warsaw_transit"]


def test_form_hides_key():
    from custom_components.warsaw_transit.config_flow import route_schema
    from homeassistant.helpers.selector import TextSelector
    schema = route_schema()
    key_selector = next(value for key, value in schema.schema.items() if str(key) == "api_key")
    assert isinstance(key_selector, TextSelector)
    assert key_selector.config["type"] == "password"
