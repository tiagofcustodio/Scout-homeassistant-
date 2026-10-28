from aiohttp import web
from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_aiohttp_proxy_stream, async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MoorebotScoutCamera(hass, entry.entry_id, data["hub_url"])])


class MoorebotScoutCamera(Camera):
    """Entidade nativa de Câmara MJPEG do Moorebot Scout no Home Assistant."""

    def __init__(self, hass: HomeAssistant, entry_id: str, hub_url: str) -> None:
        super().__init__()
        self.hass = hass
        self._hub_url = hub_url
        self._attr_unique_id = f"{entry_id}_camera"
        self._attr_name = "Moorebot Scout Câmara"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Moorebot Scout AI",
            manufacturer="Pilot Labs / Moorebot",
            model="Scout (Roller Eye ROS 1)",
            configuration_url=f"{hub_url}/ha",
        )

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(f"{self._hub_url}/api/snapshot.jpg", timeout=5) as resp:
                if resp.status == 200:
                    return await resp.read()
        except Exception:
            pass
        return None

    async def handle_async_mjpeg_stream(self, request: web.Request) -> web.StreamResponse:
        websession = async_get_clientsession(self.hass)
        stream_coro = websession.get(f"{self._hub_url}/api/video.mjpeg")
        return await async_aiohttp_proxy_stream(
            self.hass,
            request,
            stream_coro,
            "multipart/x-mixed-replace; boundary=frame",
        )
