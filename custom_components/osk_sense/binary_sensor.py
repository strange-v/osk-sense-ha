"""Binary sensor entities for OSK Sense nodes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import BinarySensorEntity

from .entity import OskSenseEntity
from .protocol import ProtocolManifest

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .api import NodeInfo
    from .runtime import GatewayRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[GatewayRuntime],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary-state fields and discover newly registered nodes."""
    runtime = entry.runtime_data
    manifest = runtime.manifest
    known: set[str] = set()

    def add_new_entities() -> None:
        entities: list[OskSenseBinarySensor] = []
        for node in runtime.registry.nodes:
            if node.state != "active" or not _profile_has_state(
                manifest, node.profile_id
            ):
                continue
            if node.device_uid in known:
                continue
            known.add(node.device_uid)
            entities.append(OskSenseBinarySensor(runtime, node))
        if entities:
            async_add_entities(entities)

    add_new_entities()
    entry.async_on_unload(runtime.async_add_listener(add_new_entities))


def _profile_has_state(manifest: ProtocolManifest, profile_id: int) -> bool:
    profile = manifest.profiles.get(profile_id)
    return profile is not None and any(
        field.get("quantity") == "binary_state" for field in profile["fields"]
    )


class OskSenseBinarySensor(OskSenseEntity, BinarySensorEntity):
    """Expose a decoded binary-state field."""

    _attr_name = "State"

    def __init__(self, runtime: GatewayRuntime, node: NodeInfo) -> None:
        super().__init__(runtime, node, "state")
        self._update_value()

    def _update_value(self) -> None:
        """Copy the latest binary state into its HA state attribute."""
        event = self._event
        if event is None or (value := event.telemetry.values.get("state")) is None:
            self._attr_is_on = None
        else:
            self._attr_is_on = bool(value)
