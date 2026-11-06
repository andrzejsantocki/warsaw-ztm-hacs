"""Cached departure timestamps and nearest approaching vehicle."""
from datetime import datetime
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(TransitSensor(coordinator, entry.entry_id, route["name"], kind) for route in entry.data["routes"] for kind in ("next_1", "next_2", "departures", "approaching_bus", "line"))
    async_add_entities([LastErrorSensor(coordinator, entry.entry_id)])


class TransitSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry_id, route, kind):
        super().__init__(coordinator)
        self.route = route
        self.kind = kind
        self._attr_unique_id = f"{entry_id}_{route}_{kind}"
        # Stable IDs regardless of the device's display name (only used for new entities).
        self.entity_id = f"sensor.{DOMAIN}_{route}_{kind}"
        self._attr_name = f"{route.replace('_', ' ')} {kind.replace('_', ' ')}"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry_id)}, "name": "Warsaw ZTM Bus Commute", "manufacturer": "City of Warsaw"}
        if kind.startswith("next_"):
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        elif kind == "line":
            self._attr_icon = "mdi:bus-marker"
        elif kind == "departures":
            self._attr_icon = "mdi:bus-clock"
        else:
            self._attr_icon = "mdi:bus"

    @property
    def native_value(self):
        if not self.coordinator.last_update_success:
            return None
        data = (self.coordinator.data or {}).get(self.route, {})
        if self.kind == "approaching_bus":
            return (data.get("gps") or {}).get("vehicle_id")
        if self.kind == "line":
            return self._line(data).get("line")
        if self.kind == "departures":
            # Plain Warsaw clock times: readable by voice assistants and LLMs,
            # unlike timestamp states, which HA always exposes in UTC.
            times = [d.get("departure_local") for d in (data.get("schedule") or [])[:2] if d.get("departure_local")]
            return ", ".join(times) or None
        schedule = (data.get("schedule") or [])
        index = int(self.kind[-1]) - 1
        if len(schedule) <= index:
            return None
        value = schedule[index].get("departure")
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError:
                return None
        return value if isinstance(value, datetime) and value.tzinfo else None

    @property
    def extra_state_attributes(self):
        data = (self.coordinator.data or {}).get(self.route, {})
        fresh = self.coordinator.last_update_success
        attributes = {"route": self.route, "status": data.get("status") if fresh else "unavailable",
                      "error": data.get("error") if fresh else "Update failed; cached data is not current",
                      "uncertainty_reason": data.get("uncertainty_reason") if fresh else "update_failed",
                      "fresh": fresh, "last_success": getattr(self.coordinator, "last_success", None)}
        if not fresh:
            return attributes
        if self.kind == "approaching_bus":
            attributes.update(data.get("gps") or {})
        elif self.kind == "line":
            attributes.update(self._line(data))
        elif self.kind == "departures":
            schedule = (data.get("schedule") or [])[:2]
            attributes.update({"lines": [d.get("line") for d in schedule], "headsigns": [d.get("headsign") for d in schedule],
                               "timezone": "Europe/Warsaw"})
        else:
            schedule = (data.get("schedule") or [])
            index = int(self.kind[-1]) - 1
            if len(schedule) > index:
                attributes.update(schedule[index])
        return attributes

    @staticmethod
    def _line(data):
        """Line of the approaching bus, else of the next scheduled departure."""
        gps = data.get("gps") or {}
        if gps.get("line"):
            return {"line": gps["line"], "source": "gps", "vehicle_id": gps.get("vehicle_id"), "brigade": gps.get("brigade")}
        schedule = data.get("schedule") or []
        if schedule and schedule[0].get("line"):
            first = schedule[0]
            return {"line": first["line"], "source": "schedule", "vehicle_id": None, "brigade": first.get("brigade"),
                    "headsign": first.get("headsign"), "departure": first.get("departure"),
                    "departure_local": first.get("departure_local")}
        return {"line": None, "source": None, "vehicle_id": None}


class LastErrorSensor(CoordinatorEntity, SensorEntity):
    """Time of the latest failed update; details and traceback in attributes."""

    _attr_has_entity_name = True
    _attr_name = "last error"
    _attr_icon = "mdi:alert-circle-outline"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"traceback"})

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_last_error"
        self.entity_id = f"sensor.{DOMAIN}_last_error"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry_id)}, "name": "Warsaw ZTM Bus Commute", "manufacturer": "City of Warsaw"}

    @property
    def available(self):
        return True

    @property
    def native_value(self):
        error = getattr(self.coordinator, "last_error", None)
        return error["time"] if error else None

    @property
    def extra_state_attributes(self):
        error = getattr(self.coordinator, "last_error", None) or {}
        return {"error_type": error.get("error_type"), "message": error.get("message"),
                "traceback": error.get("traceback"), "error_count": getattr(self.coordinator, "error_count", 0),
                "currently_failing": not self.coordinator.last_update_success}
