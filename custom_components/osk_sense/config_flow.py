"""Config flow for OSK Sense."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, UnitOfEnergy, UnitOfVolume
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,  # pyright: ignore[reportUnknownVariableType]
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,  # pyright: ignore[reportUnknownVariableType]
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import (
    ApiResponseError,
    AuthenticationError,
    CannotConnectError,
    GatewayApiClient,
    InvalidResponseError,
    UnsupportedVersionError,
)
from .const import (
    CONF_DEVICE_CLASS,
    CONF_PULSE_COUNTERS,
    CONF_TOKEN,
    CONF_UNIT,
    CONF_UNITS_PER_PULSE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_TOKEN): str,
    }
)

_ENERGY_UNITS = tuple(unit.value for unit in UnitOfEnergy)
_VOLUME_UNITS = tuple(unit.value for unit in UnitOfVolume)
_DEVICE_CLASSES = ("water", "gas", "energy", "volume", "none")


class OskSenseConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle an OSK Sense config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OskSenseOptionsFlow:
        """Return the pulse-counter options flow."""
        return OskSenseOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure a gateway manually."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                client = GatewayApiClient(
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
            except ApiResponseError, InvalidResponseError:
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


class OskSenseOptionsFlow(OptionsFlowWithReload):
    """Configure converted totals for pulse-counter nodes."""

    def __init__(self) -> None:
        self._device_uid: str | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the pulse-counter node to configure."""
        runtime = getattr(self.config_entry, "runtime_data", None)
        if runtime is None:
            return self.async_abort(reason="not_loaded")
        nodes = tuple(
            node
            for node in runtime.registry.nodes
            if node.state == "active" and node.profile_id == 6
        )
        if not nodes:
            return self.async_abort(reason="no_pulse_counters")
        if user_input is not None:
            self._device_uid = user_input["device_uid"]
            return await self.async_step_counter()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("device_uid"): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {
                                    "value": node.device_uid,
                                    "label": node.display_name
                                    or f"OSK Sense node {node.node_id}",
                                }
                                for node in nodes
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_counter(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure conversion for the selected counter."""
        assert self._device_uid is not None
        errors: dict[str, str] = {}
        counters = dict(self.config_entry.options.get(CONF_PULSE_COUNTERS, {}))
        current = counters.get(self._device_uid, {})
        if user_input is not None:
            device_class = user_input[CONF_DEVICE_CLASS]
            unit = user_input[CONF_UNIT].strip()
            if not unit:
                errors[CONF_UNIT] = "unit_required"
            elif device_class == "energy" and unit not in _ENERGY_UNITS:
                errors[CONF_UNIT] = "incompatible_unit"
            elif device_class in {"water", "gas", "volume"} and unit not in (
                _VOLUME_UNITS
            ):
                errors[CONF_UNIT] = "incompatible_unit"
            else:
                counters[self._device_uid] = {
                    CONF_UNITS_PER_PULSE: user_input[CONF_UNITS_PER_PULSE],
                    CONF_UNIT: unit,
                    CONF_DEVICE_CLASS: device_class,
                }
                options = dict(self.config_entry.options)
                options[CONF_PULSE_COUNTERS] = counters
                return self.async_create_entry(data=options)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_UNITS_PER_PULSE,
                    default=current.get(CONF_UNITS_PER_PULSE, 1.0),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0.000001,
                        step="any",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_DEVICE_CLASS,
                    default=current.get(CONF_DEVICE_CLASS, "volume"),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=list(_DEVICE_CLASSES),
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="pulse_device_class",
                    )
                ),
                vol.Required(
                    CONF_UNIT,
                    default=current.get(CONF_UNIT, UnitOfVolume.LITERS),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[*_VOLUME_UNITS, *_ENERGY_UNITS],
                        custom_value=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="counter", data_schema=schema, errors=errors
        )
