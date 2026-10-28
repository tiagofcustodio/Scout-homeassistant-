from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

PERSONALITY_MAP = {
    "friendly": "🤖 Companheiro Amigável",
    "butler": "🎩 Mordomo Jarvis",
    "sentinel": "🛡️ Sentinela Tático",
    "comedian": "😂 Comediante Sarcástico",
    "pet": "🐾 Mascote Curioso",
}
REVERSE_MAP = {v: k for k, v in PERSONALITY_MAP.items()}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MoorebotScoutPersonalitySelect(hass, data["coordinator"], entry.entry_id, data["hub_url"])])


class MoorebotScoutPersonalitySelect(CoordinatorEntity, SelectEntity):
    """Seletor da Personalidade IA do Moorebot Scout no Home Assistant."""

    def __init__(self, hass: HomeAssistant, coordinator, entry_id: str, hub_url: str):
        super().__init__(coordinator)
        self.hass = hass
        self._hub_url = hub_url
        self._attr_unique_id = f"{entry_id}_select_personality"
        self._attr_name = "Scout Personalidade IA"
        self._attr_icon = "mdi:drama-masks"
        self._attr_options = list(PERSONALITY_MAP.values())
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Moorebot Scout AI",
            manufacturer="Pilot Labs / Moorebot",
            model="Scout (Roller Eye ROS 1)",
            configuration_url=f"{hub_url}/ha",
        )

    @property
    def current_option(self) -> str:
        active_id = (
            (self.coordinator.data or {})
            .get("domestic", {})
            .get("personality", {})
            .get("active_id", "friendly")
        )
        return PERSONALITY_MAP.get(active_id, PERSONALITY_MAP["friendly"])

    async def async_select_option(self, option: str) -> None:
        pid = REVERSE_MAP.get(option, "friendly")
        session = async_get_clientsession(self.hass)
        await session.post(
            f"{self._hub_url}/api/domestic/personality",
            json={"personality_id": pid, "speak_greeting": True},
            timeout=8,
        )
        await self.coordinator.async_request_refresh()
