"""Setup retries retain real transport cooldowns under real Home Assistant."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState, current_entry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.warsaw_transit import async_setup_entry, async_unload_entry


@pytest.mark.asyncio
async def test_setup_retry_honors_retry_after_without_second_http_call(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    entry = SimpleNamespace(entry_id="entry", data={"api_key": "test-key", "routes": [
        {"name": "a", "start_stop": "A", "end_stop": "B"},
        {"name": "b", "start_stop": "B", "end_stop": "A"},
    ]})
    entry.state = ConfigEntryState.SETUP_IN_PROGRESS
    entry.async_on_unload = Mock()
    token = current_entry.set(entry)
    response = SimpleNamespace(status=429, headers={"Retry-After": "120"})
    context = AsyncMock()
    context.__aenter__.return_value = response
    session = Mock()
    session.post.return_value = context
    try:
        with patch("custom_components.warsaw_transit.coordinator.async_get_clientsession", return_value=session):
            for _ in range(2):
                with pytest.raises(ConfigEntryNotReady):
                    await async_setup_entry(hass, entry)
        assert session.post.call_count == 1
    finally:
        current_entry.reset(token)
        await hass.async_stop()


@pytest.mark.asyncio
async def test_unload_discards_pending_client_without_loaded_coordinator():
    hass = SimpleNamespace(
        data={"warsaw_transit_setup_clients": {"entry": object()}},
        config_entries=SimpleNamespace(async_unload_platforms=AsyncMock(return_value=True)),
    )
    assert await async_unload_entry(hass, SimpleNamespace(entry_id="entry"))
    assert not hass.data["warsaw_transit_setup_clients"]


@pytest.mark.asyncio
async def test_setup_key_change_replaces_client_and_success_clears_pending():
    old_client = SimpleNamespace(_key="old-key")
    new_client = SimpleNamespace(_key="new-key")
    coordinator = SimpleNamespace(client=new_client, async_config_entry_first_refresh=AsyncMock(), async_shutdown=AsyncMock())
    hass = SimpleNamespace(
        data={"warsaw_transit_setup_clients": {"entry": old_client}},
        http=SimpleNamespace(register_view=Mock()),
        config_entries=SimpleNamespace(async_forward_entry_setups=AsyncMock()),
    )
    entry = SimpleNamespace(entry_id="entry", data={"api_key": "new-key"}, async_on_unload=Mock(), add_update_listener=Mock())
    with patch("custom_components.warsaw_transit.coordinator.TransitCoordinator", return_value=coordinator) as factory:
        assert await async_setup_entry(hass, entry)
    factory.assert_called_once_with(hass, entry, client=None)
    assert not hass.data["warsaw_transit_setup_clients"]
    assert hass.data["warsaw_transit"]["entry"] is coordinator
    coordinator.async_shutdown.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fresh, expected", [(True, "gps_stale"), (False, "update_failed")])
async def test_cached_http_exposes_uncertainty_reason(fresh, expected):
    import json
    from custom_components.warsaw_transit import TransitView

    coordinator = SimpleNamespace(
        data={"a": {"gps": None, "status": "unknown", "uncertainty_reason": "gps_stale"}},
        last_update_success=fresh,
    )
    request = SimpleNamespace(app={"hass": SimpleNamespace(data={"warsaw_transit": {"entry": coordinator}})})
    response = await TransitView("gps").get(request, "entry", "a")
    assert json.loads(response.body)["uncertainty_reason"] == expected
