from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    hub_url = data["hub_url"]
    eid = entry.entry_id

    buttons = [
        # Navegação Mecanum
        MoorebotScoutButton(hass, eid, hub_url, "move_fwd", "Scout Mover Frente", "mdi:arrow-up-bold", "/api/move", {"linear_x": 0.35, "linear_y": 0.0, "angular_z": 0.0, "duration_s": 0.9}),
        MoorebotScoutButton(hass, eid, hub_url, "move_back", "Scout Mover Trás", "mdi:arrow-down-bold", "/api/move", {"linear_x": -0.35, "linear_y": 0.0, "angular_z": 0.0, "duration_s": 0.9}),
        MoorebotScoutButton(hass, eid, hub_url, "strafe_left", "Scout Strafe Esquerda", "mdi:arrow-left-bold", "/api/move", {"linear_x": 0.0, "linear_y": 0.35, "angular_z": 0.0, "duration_s": 0.9}),
        MoorebotScoutButton(hass, eid, hub_url, "strafe_right", "Scout Strafe Direita", "mdi:arrow-right-bold", "/api/move", {"linear_x": 0.0, "linear_y": -0.35, "angular_z": 0.0, "duration_s": 0.9}),
        MoorebotScoutButton(hass, eid, hub_url, "rot_left", "Scout Rodar Esquerda", "mdi:rotate-left", "/api/move", {"linear_x": 0.0, "linear_y": 0.0, "angular_z": 0.85, "duration_s": 0.8}),
        MoorebotScoutButton(hass, eid, hub_url, "rot_right", "Scout Rodar Direita", "mdi:rotate-right", "/api/move", {"linear_x": 0.0, "linear_y": 0.0, "angular_z": -0.85, "duration_s": 0.8}),
        MoorebotScoutButton(hass, eid, hub_url, "stop", "Scout Parar Emergência", "mdi:Octagon", "/api/stop", {}),
        # Ações & Coreografias
        MoorebotScoutButton(hass, eid, hub_url, "snapshot", "Scout Tirar Fotografia", "mdi:camera-iris", "/api/domestic/snapshot", {"label": "Home Assistant", "speak_shutter": True}),
        MoorebotScoutButton(hass, eid, hub_url, "wake_mic", "Scout Acordar Microfone (30s)", "mdi:microphone", "/api/audio/settings", {"wake_now": True}),
        MoorebotScoutButton(hass, eid, hub_url, "stunt_dance", "Scout Coreografia: Dançar", "mdi:dance-ballroom", "/api/domestic/stunt", {"stunt": "dance"}),
        MoorebotScoutButton(hass, eid, hub_url, "stunt_salute", "Scout Coreografia: Saudação", "mdi:hand-wave", "/api/domestic/stunt", {"stunt": "salute"}),
        MoorebotScoutButton(hass, eid, hub_url, "stunt_spin", "Scout Coreografia: Pirueta 360", "mdi:sync", "/api/domestic/stunt", {"stunt": "spin360"}),
    ]
    async_add_entities(buttons)


class MoorebotScoutButton(ButtonEntity):
    """Botão de Ação Rápida do Moorebot Scout no Home Assistant."""

    def __init__(self, hass: HomeAssistant, entry_id: str, hub_url: str, key: str, name: str, icon: str, endpoint: str, payload: dict):
        self.hass = hass
        self._hub_url = hub_url
        self._endpoint = endpoint
        self._payload = payload
        self._attr_unique_id = f"{entry_id}_btn_{key}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Moorebot Scout AI",
            manufacturer="Pilot Labs / Moorebot",
            model="Scout (Roller Eye ROS 1)",
            configuration_url=f"{hub_url}/ha",
        )

    async def async_press(self) -> None:
        session = async_get_clientsession(self.hass)
        await session.post(f"{self._hub_url}{self._endpoint}", json=self._payload, timeout=10)
