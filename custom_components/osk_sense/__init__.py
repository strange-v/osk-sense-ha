"""OSK Sense Home Assistant integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

from .api import (
    ApiResponseError,
    AuthenticationError,
    CannotConnectError,
    GatewayBootstrap,
    InvalidResponseError,
    OskSenseApiClient,
    UnsupportedVersionError,
)
from .const import CONF_TOKEN, DOMAIN, MANUFACTURER


@dataclass(frozen=True, slots=True)
class OskSenseRuntimeData:
    """Runtime state owned by one config entry."""

    client: OskSenseApiClient
    bootstrap: GatewayBootstrap


if TYPE_CHECKING:
    type OskSenseConfigEntry = ConfigEntry[OskSenseRuntimeData]
else:
    type OskSenseConfigEntry = Any


async def async_setup_entry(hass: HomeAssistant, entry: OskSenseConfigEntry) -> bool:
    """Set up an OSK Sense gateway from a config entry."""
    from homeassistant.const import CONF_HOST
    from homeassistant.exceptions import (
        ConfigEntryAuthFailed,
        ConfigEntryError,
        ConfigEntryNotReady,
    )
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    client = OskSenseApiClient(
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

    entry.runtime_data = OskSenseRuntimeData(client, bootstrap)
    _async_register_devices(hass, entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: OskSenseConfigEntry) -> bool:
    """Unload an OSK Sense config entry."""
    return True


def _async_register_devices(hass: HomeAssistant, entry: OskSenseConfigEntry) -> None:
    """Register the gateway and currently active nodes."""
    from homeassistant.helpers import device_registry as dr

    bootstrap = entry.runtime_data.bootstrap
    info = bootstrap.info
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
    for node in bootstrap.registry.nodes:
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
