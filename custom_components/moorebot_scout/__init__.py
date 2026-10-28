from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_HUB_URL, DOMAIN, PLATFORMS

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Configura a integração Moorebot Scout AI a partir de uma ConfigEntry."""
    hub_url = entry.data[CONF_HUB_URL].rstrip("/")
    session = async_get_clientsession(hass)

    async def _async_update_data():
        try:
            async with session.get(f"{hub_url}/api/status", timeout=6) as resp:
                if resp.status != 200:
                    raise UpdateFailed(f"HTTP {resp.status}")
                return await resp.json()
        except Exception as err:
            raise UpdateFailed(f"Erro ao comunicar com o servidor Scout ({hub_url}): {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=_async_update_data,
        update_interval=timedelta(seconds=2),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "hub_url": hub_url,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Regista serviços globais do domínio moorebot_scout
    async def handle_speak(call: ServiceCall):
        text = call.data.get("text", "")
        if text:
            await session.post(f"{hub_url}/api/speak", json={"text": text}, timeout=10)

    async def handle_ask_ai(call: ServiceCall):
        prompt = call.data.get("prompt", "")
        if prompt:
            await session.post(
                f"{hub_url}/api/ai/chat",
                json={"prompt": prompt, "include_vision": True, "speak_on_scout": True},
                timeout=30,
            )

    async def handle_stunt(call: ServiceCall):
        stunt = call.data.get("stunt", "dance")
        await session.post(f"{hub_url}/api/domestic/stunt", json={"stunt": stunt}, timeout=10)

    if not hass.services.has_service(DOMAIN, "speak"):
        hass.services.async_register(DOMAIN, "speak", handle_speak)
    if not hass.services.has_service(DOMAIN, "ask_ai"):
        hass.services.async_register(DOMAIN, "ask_ai", handle_ask_ai)
    if not hass.services.has_service(DOMAIN, "stunt"):
        hass.services.async_register(DOMAIN, "stunt", handle_stunt)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
