import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_HUB_URL, DEFAULT_HUB_URL, DOMAIN


class MoorebotScoutConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Fluxo de configuração visual para o Moorebot Scout AI no Home Assistant."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            hub_url = user_input[CONF_HUB_URL].rstrip("/")
            session = async_get_clientsession(self.hass)
            try:
                async with session.get(f"{hub_url}/api/status", timeout=8) as resp:
                    if resp.status == 200:
                        await self.async_set_unique_id(f"moorebot_scout_{hub_url}")
                        self._abort_if_unique_id_configured()
                        return self.async_create_entry(
                            title=f"Moorebot Scout AI ({hub_url})",
                            data={CONF_HUB_URL: hub_url},
                        )
                    errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "cannot_connect"

        schema = vol.Schema(
            {
                vol.Required(CONF_HUB_URL, default=DEFAULT_HUB_URL): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
