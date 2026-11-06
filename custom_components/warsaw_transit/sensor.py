"""Cached departure timestamps and nearest approaching vehicle."""
from datetime import datetime
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(TransitSensor(coordinator, entry.entry_id, route["name"], kind) for route in entry.data["routes"] for kind in ("next_1", "next_2", "approaching_bus", "line"))


class TransitSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry_id, route, kind):
        super().__init__(coordinator)
        self.route = route
        self.kind = kind
        self._attr_unique_id = f"{entry_id}_{route}_{kind}"
        self._attr_name = f"{route.replace('_', ' ')} {kind.replace('_', ' ')}"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry_id)}, "name": "Warsaw Transit", "manufacturer": "City of Warsaw"}
        if kind.startswith("next_"):
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        elif kind == "line":
            self._attr_icon = "mdi:bus-marker"
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
                    "headsign": first.get("headsign"), "departure": first.get("departure")}
        return {"line": None, "source": None, "vehicle_id": None}
