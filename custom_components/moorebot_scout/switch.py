from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
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

    switches = [
        MoorebotScoutPatrolSwitch(hass, coord, entry.entry_id, hub_url),
        MoorebotScoutSentrySwitch(hass, coord, entry.entry_id, hub_url),
        MoorebotScoutIRSwitch(hass, coord, entry.entry_id, hub_url),
    ]
    async_add_entities(switches)


class _BaseScoutSwitch(CoordinatorEntity, SwitchEntity):
    def __init__(self, hass: HomeAssistant, coordinator, entry_id: str, hub_url: str, key: str, name: str, icon: str):
        super().__init__(coordinator)
        self.hass = hass
        self._hub_url = hub_url
        self._attr_unique_id = f"{entry_id}_switch_{key}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Moorebot Scout AI",
            manufacturer="Pilot Labs / Moorebot",
            model="Scout (Roller Eye ROS 1)",
            configuration_url=f"{hub_url}/ha",
        )


class MoorebotScoutPatrolSwitch(_BaseScoutSwitch):
    """Switch para Iniciar/Parar Patrulha Autónoma & Mapa 2D."""

    def __init__(self, hass, coord, entry_id, hub_url):
        super().__init__(hass, coord, entry_id, hub_url, "patrol", "Scout Patrulha Autónoma 2D", "mdi:robot-mower")

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("map", {}).get("patrolling"))

    async def async_turn_on(self, **kwargs) -> None:
        session = async_get_clientsession(self.hass)
        await session.post(f"{self._hub_url}/api/patrol/start", timeout=8)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        session = async_get_clientsession(self.hass)
        await session.post(f"{self._hub_url}/api/patrol/stop", timeout=8)
        await self.coordinator.async_request_refresh()


class MoorebotScoutSentrySwitch(_BaseScoutSwitch):
    """Switch para Ligar/Desligar Modo Sentinela (Alarme Residencial)."""

    def __init__(self, hass, coord, entry_id, hub_url):
        super().__init__(hass, coord, entry_id, hub_url, "sentry", "Scout Modo Sentinela (Alarme)", "mdi:shield-Alert")

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("domestic", {}).get("sentry", {}).get("enabled"))

    async def async_turn_on(self, **kwargs) -> None:
        session = async_get_clientsession(self.hass)
        await session.post(f"{self._hub_url}/api/domestic/sentry", json={"enabled": True}, timeout=8)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        session = async_get_clientsession(self.hass)
        await session.post(f"{self._hub_url}/api/domestic/sentry", json={"enabled": False}, timeout=8)
        await self.coordinator.async_request_refresh()


class MoorebotScoutIRSwitch(_BaseScoutSwitch):
    """Switch para Ligar/Desligar Visão Noturna Infravermelhos (IR)."""

    def __init__(self, hass, coord, entry_id, hub_url):
        super().__init__(hass, coord, entry_id, hub_url, "night_vision", "Scout Visão Noturna (IR)", "mdi:weather-night")

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("robot", {}).get("ir_led_on"))

    async def async_turn_on(self, **kwargs) -> None:
        session = async_get_clientsession(self.hass)
        await session.post(f"{self._hub_url}/api/night-vision?enable=true", timeout=8)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        session = async_get_clientsession(self.hass)
        await session.post(f"{self._hub_url}/api/night-vision?enable=false", timeout=8)
        await self.coordinator.async_request_refresh()
