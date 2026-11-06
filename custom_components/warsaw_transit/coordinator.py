"""One shared polling coordinator per entry."""
from datetime import datetime, timedelta, timezone
import logging
import traceback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .api import TransitClient, TransitError, AuthError, CooldownError, ConfigurationError
from .engine import TransitEngine, WARSAW
from .models import RouteConfig
from .const import DOMAIN, UPDATE_SECONDS

MAX_TRACEBACK = 8000

_LOGGER = logging.getLogger(__name__)


class TransitCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, client=None):
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=UPDATE_SECONDS))
        self.client = client if client is not None else TransitClient(async_get_clientsession(hass), entry.data["api_key"])
        self.engine = TransitEngine(self.client, [RouteConfig(**route) for route in entry.data["routes"]])
        self._api_key = entry.data["api_key"]
        self._initialized = False
        self._configuration_error = None
        self.last_success = None
        self.last_error = None
        self.error_count = 0

    async def _async_update_data(self):
        try:
            return await self._async_fetch()
        except Exception as err:
            self._record_error(err)
            raise

    def _redact(self, text):
        return text.replace(self._api_key, "[redacted]") if self._api_key else text

    def _record_error(self, err):
        """Keep the latest failure (with its cause chain) for the diagnostic sensor."""
        self.error_count += 1
        self.last_error = {
            "time": datetime.now(timezone.utc),
            "error_type": type(err).__name__,
            "message": self._redact(str(err)),
            "traceback": self._redact("".join(traceback.format_exception(err)))[-MAX_TRACEBACK:],
        }

    async def _async_fetch(self):
        if self._configuration_error is not None:
            raise ConfigEntryError(self._configuration_error)
        try:
            if not self._initialized:
                await self.engine.async_setup()
                self._initialized = True
            data = await self.engine.async_refresh()
            self.last_success = datetime.now(WARSAW).isoformat()
            return data
        except AuthError as err:
            raise ConfigEntryAuthFailed("API key rejected. Reauthenticate with a current Warsaw Open Data key.") from err
        except ConfigurationError as err:
            self._configuration_error = str(err).replace(self._api_key, "[redacted]")
            self.update_interval = None
            raise ConfigEntryError(self._configuration_error) from None
        except CooldownError as err:
            raise UpdateFailed("Warsaw API cooldown active. Automatic polling will resume when allowed.") from err
        except (TransitError, TimeoutError, OSError) as err:
            raise UpdateFailed("Warsaw API unavailable. Check connectivity; the integration will retry automatically.") from err
