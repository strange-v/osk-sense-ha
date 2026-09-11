"""OSK Sense Home Assistant integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

from .api import (
    ApiResponseError,
    AuthenticationError,
    CannotConnectError,
    GatewayApiClient,
    InvalidResponseError,
    UnsupportedVersionError,
)
from .const import CONF_TOKEN, DOMAIN, MANUFACTURER
from .protocol import ProtocolManifest
from .runtime import GatewayRuntime

PLATFORMS = ("sensor", "binary_sensor")

if TYPE_CHECKING:
    type IntegrationConfigEntry = ConfigEntry[GatewayRuntime]
else:
    type IntegrationConfigEntry = Any


async def async_setup_entry(hass: HomeAssistant, entry: IntegrationConfigEntry) -> bool:
    """Set up an OSK Sense gateway from a config entry."""
    from homeassistant.const import CONF_HOST
    from homeassistant.core import callback
    from homeassistant.exceptions import (
        ConfigEntryAuthFailed,
        ConfigEntryError,
        ConfigEntryNotReady,
    )
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    from homeassistant.helpers.start import async_at_started

    client = GatewayApiClient(
        entry.data[CONF_HOST],
        entry.data[CONF_TOKEN],
        async_get_clientsession(hass),
    )
    try:
        bootstrap = await client.async_bootstrap()
    except AuthenticationError as error:
        raise ConfigEntryAuthFailed("Invalid OSK Sense API token") from error
    except CannotConnectError as error:
        raise ConfigEntryNotReady("Cannot connect to OSK Sense Hub") from error
    except (ApiResponseError, InvalidResponseError, UnsupportedVersionError) as error:
        raise ConfigEntryError("Invalid or unsupported OSK Sense response") from error

    manifest = await hass.async_add_executor_job(ProtocolManifest.load_default)
    entry.runtime_data = GatewayRuntime(client, bootstrap, manifest=manifest)
    _async_register_devices(hass, entry)
    registered_state = [bootstrap.info, bootstrap.registry]

    def async_refresh_devices() -> None:
        runtime = entry.runtime_data
        current_state = [runtime.bootstrap.info, runtime.registry]
        if current_state != registered_state:
            _async_register_devices(hass, entry)
            registered_state[:] = current_state

    entry.runtime_data.async_add_listener(async_refresh_devices)

    @callback
    def async_start_stream(_: HomeAssistant) -> None:
        entry.async_create_background_task(
            hass,
            entry.runtime_data.async_run(),
            f"{DOMAIN} stream {entry.entry_id}",
        )

    entry.async_on_unload(async_at_started(hass, async_start_stream))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: IntegrationConfigEntry
) -> bool:
    """Unload an OSK Sense config entry."""
    await entry.runtime_data.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _async_register_devices(hass: HomeAssistant, entry: IntegrationConfigEntry) -> None:
    """Register the gateway and currently active nodes."""
    from homeassistant.helpers import device_registry as dr

    runtime = entry.runtime_data
    info = runtime.bootstrap.info
    registry = dr.async_get(hass)
    gateway = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        configuration_url=entry.runtime_data.client.base_url,
        identifiers={(DOMAIN, info.gateway_id)},
        manufacturer=MANUFACTURER,
        model=info.board,
        name=info.hostname,
        serial_number=info.gateway_id,
        sw_version=info.firmware_version,
    )
    for node in runtime.registry.nodes:
        if node.state != "active":
            continue
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, node.device_uid)},
            manufacturer=MANUFACTURER,
            model=f"OSK Sense profile {node.profile_id}",
            name=node.display_name or f"OSK Sense node {node.node_id}",
            serial_number=node.device_uid,
            sw_version=node.firmware,
            via_device_id=gateway.id,
        )
