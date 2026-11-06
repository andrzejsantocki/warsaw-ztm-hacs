"""Two-route UI configuration and reconfiguration."""
import re
import voluptuous as vol
from pydantic import ValidationError
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType
from .const import DOMAIN, DEFAULT_NAMES


def route_schema(data=None, user_input=None):
    data = data or {}
    fields = {vol.Required("api_key", default=data.get("api_key", "")): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
    routes = data.get("routes", [])
    for i, name in enumerate(DEFAULT_NAMES, 1):
        route = routes[i - 1] if len(routes) >= i else {"name": name}
        for field in ("name", "start_stop", "end_stop", "start_post", "end_post"):
            marker = vol.Optional if field.endswith("post") else vol.Required
            fields[marker(f"route_{i}_{field}", default=route.get(field) or "")] = str
    if user_input is not None:
        fields = {type(marker)(str(marker), default="" if str(marker) == "api_key" else user_input.get(str(marker), "")): selector for marker, selector in fields.items()}
    return vol.Schema(fields)


def route_data(user_input):
    key = user_input["api_key"].strip()
    if not key:
        raise vol.Invalid("Enter your Warsaw Open Data API key")
    routes = []
    for i in (1, 2):
        route = {field: user_input.get(f"route_{i}_{field}", "").strip() for field in ("name", "start_stop", "end_stop", "start_post", "end_post")}
        if not re.fullmatch(r"[a-z0-9_]+", route["name"]):
            raise vol.Invalid("Route names must use lowercase letters, digits and underscores")
        if not route["start_stop"] or not route["end_stop"]:
            raise vol.Invalid("Enter both stop names for each route")
        for field in ("start_post", "end_post"):
            route[field] = route[field] or None
        routes.append(route)
    if routes[0]["name"] == routes[1]["name"]:
        raise vol.Invalid("Choose a different name for each route")
    return {"api_key": key, "routes": routes}


class WarsawTransitConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        super().__init__()
        self._client = None
        self._client_key = None

    async def _form(self, step, user_input, entry=None):
        errors = {}
        placeholders = {"detail": ""}
        if user_input is not None:
            from .api import AuthError, ConfigurationError, CooldownError, TransitClient, TransitError
            from .engine import TransitEngine
            from .models import RouteConfig
            try:
                data = route_data(user_input)
                routes = [RouteConfig(**route) for route in data["routes"]]
                if self._client is None or self._client_key != data["api_key"]:
                    self._client = TransitClient(async_get_clientsession(self.hass), data["api_key"])
                    self._client_key = data["api_key"]
                engine = TransitEngine(self._client, routes)
                await engine.async_setup()
            except ValidationError as err:
                errors["base"] = "invalid_routes"
                # Do not render inputs, exception contexts, or custom validator messages.
                placeholders["detail"] = "; ".join(
                    f"{'.'.join(map(str, item['loc']))}: invalid value ({item['type']})"
                    for item in err.errors(include_input=False, include_context=False, include_url=False)
                )
            except vol.Invalid as err:
                errors["base"] = "invalid_routes"
                placeholders["detail"] = str(err)
            except AuthError:
                errors["base"] = "invalid_auth"
            except ConfigurationError as err:
                errors["base"] = "invalid_stops"
                placeholders["detail"] = str(err)
            except CooldownError:
                errors["base"] = "cooldown"
            except (TransitError, TimeoutError, OSError):
                errors["base"] = "cannot_connect"
            else:
                if entry:
                    return self.async_update_reload_and_abort(entry, data_updates=data)
                return self.async_create_entry(title="Warsaw Transit", data=data)
        if user_input is not None:
            key = user_input.get("api_key", "").strip()
            if key:
                placeholders["detail"] = placeholders["detail"].replace(key, "[redacted]")
        return self.async_show_form(step_id=step, data_schema=route_schema(entry.data if entry else None, user_input), errors=errors, description_placeholders=placeholders)

    async def async_step_user(self, user_input=None):
        return await self._form("user", user_input)

    async def async_step_reconfigure(self, user_input=None):
        return await self._form("reconfigure", user_input, self._get_reconfigure_entry())

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        return await self._form("reauth_confirm", user_input, self._get_reauth_entry())
