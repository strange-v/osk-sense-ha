"""Config flow for OSK Sense."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_IGNORE,
    SOURCE_ZEROCONF,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, UnitOfEnergy, UnitOfVolume
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,  # pyright: ignore[reportUnknownVariableType]
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,  # pyright: ignore[reportUnknownVariableType]
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from yarl import URL

from .api import (
    SUPPORTED_API_VERSIONS,
    SUPPORTED_STREAM_VERSIONS,
    ApiResponseError,
    AuthenticationError,
    CannotConnectError,
    GatewayApiClient,
    GatewayIdentityError,
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
_TOKEN_SCHEMA = vol.Schema({vol.Required(CONF_TOKEN): str})
_GATEWAY_ID = re.compile(r"[0-9a-f]{32}\Z")

_ENERGY_UNITS = tuple(unit.value for unit in UnitOfEnergy)
_VOLUME_UNITS = tuple(unit.value for unit in UnitOfVolume)
_DEVICE_CLASSES = ("water", "gas", "energy", "volume", "none")


class OskSenseConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle an OSK Sense config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_host: str | None = None
        self._discovered_gateway_id: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OskSenseOptionsFlow:
        """Return the pulse-counter options flow."""
        return OskSenseOptionsFlow()

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Verify a discovered gateway and update an existing entry's address."""
        advertised_id = discovery_info.properties.get("gateway_id")
        if (
            not isinstance(advertised_id, str)
            or _GATEWAY_ID.fullmatch(advertised_id) is None
            or discovery_info.port is None
            or not 1 <= discovery_info.port <= 65535
        ):
            return self.async_abort(reason="invalid_discovery")

        host = str(
            URL.build(
                scheme="http",
                host=discovery_info.host,
                port=discovery_info.port,
            )
        ).rstrip("/")
        try:
            client = GatewayApiClient(host, None, async_get_clientsession(self.hass))
            info = await client.async_get_info()
        except ValueError, CannotConnectError, ApiResponseError, InvalidResponseError:
            return self.async_abort(reason="invalid_discovery")
        if info.gateway_id != advertised_id:
            return self.async_abort(reason="invalid_discovery")
        if (
            info.api_version not in SUPPORTED_API_VERSIONS
            or info.stream_version not in SUPPORTED_STREAM_VERSIONS
        ):
            return self.async_abort(reason="unsupported_version")

        existing_entry = await self.async_set_unique_id(
            info.gateway_id, raise_on_progress=False
        )
        if existing_entry is not None and existing_entry.source == SOURCE_IGNORE:
            return self.async_abort(reason="already_configured")
        self._abort_if_unique_id_configured(updates={CONF_HOST: client.base_url})
        placeholders = {"name": info.hostname, "host": client.base_url}
        self._replace_stale_discovery(info.gateway_id, placeholders)
        self._discovered_host = client.base_url
        self._discovered_gateway_id = info.gateway_id
        self.context["title_placeholders"] = placeholders
        return await self.async_step_zeroconf_confirm()

    def _replace_stale_discovery(
        self, gateway_id: str, placeholders: dict[str, str]
    ) -> None:
        """Replace pending discoveries for this gateway or its current address.

        A card for the same gateway is stale when its hostname or address has
        changed. A card for another gateway ID at this address can no longer be
        completed, because its confirm step verifies the gateway identity.
        """
        stale: list[str] = []
        for flow in self._async_in_progress(
            include_uninitialized=True, match_context={"source": SOURCE_ZEROCONF}
        ):
            context = flow.get("context", {})
            flow_placeholders = context.get("title_placeholders") or {}
            if context.get("unique_id") == gateway_id:
                if flow_placeholders == placeholders:
                    raise AbortFlow("already_in_progress")
                stale.append(flow["flow_id"])
            elif flow_placeholders.get("host") == placeholders["host"]:
                stale.append(flow["flow_id"])
        for flow_id in stale:
            self.hass.config_entries.flow.async_abort(flow_id)

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect a token for a verified, newly discovered gateway."""
        if self._discovered_host is None or self._discovered_gateway_id is None:
            return self.async_abort(reason="invalid_discovery")
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                client = GatewayApiClient(
                    self._discovered_host,
                    user_input[CONF_TOKEN],
                    async_get_clientsession(self.hass),
                )
                bootstrap = await client.async_bootstrap(
                    expected_gateway_id=self._discovered_gateway_id
                )
            except ValueError, AuthenticationError:
                errors["base"] = "invalid_auth"
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            except UnsupportedVersionError:
                errors["base"] = "unsupported_version"
            except GatewayIdentityError:
                errors["base"] = "wrong_gateway"
            except ApiResponseError, InvalidResponseError:
                errors["base"] = "invalid_response"
            except Exception:
                _LOGGER.exception(
                    "Unexpected error configuring discovered OSK Sense Hub"
                )
                errors["base"] = "unknown"
            else:
                if bootstrap.info.gateway_id != self._discovered_gateway_id:
                    errors["base"] = "wrong_gateway"
                else:
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=bootstrap.info.hostname,
                        data={
                            CONF_HOST: client.base_url,
                            CONF_TOKEN: user_input[CONF_TOKEN],
                        },
                    )

        return self.async_show_form(
            step_id="zeroconf_confirm",
            data_schema=_TOKEN_SCHEMA,
            errors=errors,
        )

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

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after the gateway rejects the token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate and store a replacement API token."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            try:
                client = GatewayApiClient(
                    entry.data[CONF_HOST],
                    user_input[CONF_TOKEN],
                    async_get_clientsession(self.hass),
                )
                bootstrap = await client.async_bootstrap()
            except ValueError, AuthenticationError:
                errors["base"] = "invalid_auth"
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            except UnsupportedVersionError:
                errors["base"] = "unsupported_version"
            except ApiResponseError, InvalidResponseError:
                errors["base"] = "invalid_response"
            except Exception:
                _LOGGER.exception(
                    "Unexpected error while reauthenticating OSK Sense Hub"
                )
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(bootstrap.info.gateway_id)
                self._abort_if_unique_id_mismatch(reason="wrong_gateway")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_TOKEN: user_input[CONF_TOKEN]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
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
