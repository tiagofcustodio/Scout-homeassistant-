from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coord = data["coordinator"]
    hub_url = data["hub_url"]

    sensors = [
        MoorebotScoutSensor(
            coord,
            entry.entry_id,
            hub_url,
            "tof_distance",
            "Scout Laser ToF Distância",
            "m",
            "mdi:laser-pointer",
            lambda d: d.get("robot", {}).get("tof_distance_m"),
        ),
        MoorebotScoutSensor(
            coord,
            entry.entry_id,
            hub_url,
            "active_person",
            "Scout Pessoa Identificada",
            None,
            "mdi:account-eye",
            lambda d: d.get("identity", {}).get("live", {}).get("active_person") or "Ninguém identificado",
        ),
        MoorebotScoutSensor(
            coord,
            entry.entry_id,
            hub_url,
            "explored_area",
            "Scout Área Mapeada 2D",
            "m²",
            "mdi:map-marker-path",
            lambda d: d.get("map", {}).get("explored_area_m2"),
        ),
        MoorebotScoutSensor(
            coord,
            entry.entry_id,
            hub_url,
            "patrol_state",
            "Scout Estado da Patrulha",
            None,
            "mdi:shield-car",
            lambda d: d.get("map", {}).get("patrol_state"),
        ),
        MoorebotScoutSensor(
            coord,
            entry.entry_id,
            hub_url,
            "light_lux",
            "Scout Luminosidade",
            "lx",
            "mdi:brightness-6",
            lambda d: d.get("robot", {}).get("light_lux"),
        ),
        MoorebotScoutSensor(
            coord,
            entry.entry_id,
            hub_url,
            "last_heard",
            "Scout Última Frase Ouvida",
            None,
            "mdi:microphone-message",
            lambda d: d.get("robot", {}).get("microphone", {}).get("last_transcript"),
        ),
        MoorebotScoutSensor(
            coord,
            entry.entry_id,
            hub_url,
            "xbox_status",
            "Scout Comando Xbox",
            None,
            "mdi:microsoft-xbox-controller",
            lambda d: "Ligado" if d.get("xbox", {}).get("connected") else "Desligado",
        ),
    ]
    async_add_entities(sensors)


class MoorebotScoutSensor(CoordinatorEntity, SensorEntity):
    """Sensor de Telemetria do Moorebot Scout no Home Assistant."""

    def __init__(self, coordinator, entry_id: str, hub_url: str, key: str, name: str, unit, icon: str, value_fn):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_sensor_{key}"
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self._value_fn = value_fn
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Moorebot Scout AI",
            manufacturer="Pilot Labs / Moorebot",
            model="Scout (Roller Eye ROS 1)",
            configuration_url=f"{hub_url}/ha",
        )

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        return self._value_fn(self.coordinator.data)
