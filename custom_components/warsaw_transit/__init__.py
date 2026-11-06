"""Home Assistant adapter for Warsaw bus routes."""
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryNotReady
from .const import DOMAIN


class TransitView(HomeAssistantView):
    """Authenticated, cache-only resource; never causes upstream requests."""
    requires_auth = True

    def __init__(self, resource):
        self.resource = resource
        self.url = f"/api/{DOMAIN}/{{entry_id}}/{{route_name}}/{resource}"
        self.name = f"api:{DOMAIN}:{resource}"

    async def get(self, request, entry_id, route_name):
        coordinator = request.app["hass"].data.get(DOMAIN, {}).get(entry_id)
        if coordinator is None or not coordinator.data or route_name not in coordinator.data:
            raise web.HTTPNotFound(reason="Unknown entry or route; check integration configuration")
        data = coordinator.data[route_name]
        fresh = coordinator.last_update_success
        return self.json({
            "entry_id": entry_id, "route": route_name,
            self.resource: data.get(self.resource) if fresh else None,
            "status": data.get("status") if fresh else "unavailable",
            "uncertainty_reason": data.get("uncertainty_reason") if fresh else "update_failed",
            "error": data.get("error") if fresh else "Update failed; cached data is not current",
            "fresh": fresh, "last_success": getattr(coordinator, "last_success", None),
        }, status_code=200 if fresh else 503)


async def async_setup_entry(hass, entry):
    from .coordinator import TransitCoordinator
    pending = hass.data.setdefault(f"{DOMAIN}_setup_clients", {})
    client = pending.pop(entry.entry_id, None)
    if client is not None and client._key != entry.data["api_key"].strip():
        client = None
    coordinator = TransitCoordinator(hass, entry, client=client)
    refreshed = False
    try:
        await coordinator.async_config_entry_first_refresh()
        refreshed = True
    except ConfigEntryNotReady:
        # Keep only transport backoff, never a failed coordinator's timers.
        pending[entry.entry_id] = coordinator.client
        raise
    finally:
        if not refreshed:
            await coordinator.async_shutdown()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    if not hass.data.get(f"{DOMAIN}_views_registered"):
        for resource in ("schedule", "gps"):
            hass.http.register_view(TransitView(resource))
        hass.data[f"{DOMAIN}_views_registered"] = True
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.SENSOR])
    return True


async def _async_reload(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry, [Platform.SENSOR]):
        hass.data.get(f"{DOMAIN}_setup_clients", {}).pop(entry.entry_id, None)
        coordinator = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if coordinator is not None:
            await coordinator.async_shutdown()
        return True
    return False
