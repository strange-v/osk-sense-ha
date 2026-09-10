"""Config flow for OSK Sense."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    ApiResponseError,
    AuthenticationError,
    CannotConnectError,
    InvalidResponseError,
    OskSenseApiClient,
    UnsupportedVersionError,
)
from .const import CONF_TOKEN, DOMAIN

_LOGGER = logging.getLogger(__name__)

_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_TOKEN): str,
    }
)


class OskSenseConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle an OSK Sense config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure a gateway manually."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                client = OskSenseApiClient(
                    user_input[CONF_HOST],
                    user_input[CONF_TOKEN],
                    async_get_clientsession(self.hass),
                )
                bootstrap = await client.async_bootstrap()
            except ValueError:
                errors["base"] = "invalid_host"
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except UnsupportedVersionError:
                errors["base"] = "unsupported_version"
            except (ApiResponseError, InvalidResponseError):
                errors["base"] = "invalid_response"
            except Exception:
                _LOGGER.exception("Unexpected error while connecting to OSK Sense Hub")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(bootstrap.info.gateway_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=bootstrap.info.hostname,
                    data={
                        CONF_HOST: client.base_url,
                        CONF_TOKEN: user_input[CONF_TOKEN],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_USER_SCHEMA,
            errors=errors,
        )
