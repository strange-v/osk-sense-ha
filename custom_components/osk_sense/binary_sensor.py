"""Binary sensor entities for OSK Sense nodes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er

from .entity import OskSenseEntity, OskSenseGatewayEntity
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
    tracked: dict[str, OskSenseBinarySensor] = {}

    async_add_entities([OskSenseGatewayConnection(runtime)])

    def reconcile_entities() -> None:
        nodes_by_uid = {node.device_uid: node for node in runtime.registry.nodes}
        desired = {
            node.device_uid: node
            for node in runtime.registry.nodes
            if node.state == "active" and _profile_has_state(manifest, node.profile_id)
        }
        entity_registry = er.async_get(hass)
        for device_uid, entity in tuple(tracked.items()):
            node = nodes_by_uid.get(device_uid)
            if node is not None and (node.state != "active" or device_uid in desired):
                continue
            tracked.pop(device_uid)
            if entity_registry.async_get(entity.entity_id) is not None:
                entity_registry.async_remove(entity.entity_id)

        entities: list[OskSenseBinarySensor] = []
        for device_uid, node in desired.items():
            if device_uid in tracked:
                continue
            entity = OskSenseBinarySensor(runtime, node)
            tracked[device_uid] = entity
            entities.append(entity)
        if entities:
            async_add_entities(entities)

    reconcile_entities()
    entry.async_on_unload(runtime.async_add_listener(reconcile_entities))


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


class OskSenseGatewayConnection(OskSenseGatewayEntity, BinarySensorEntity):
    """Report whether the gateway telemetry stream is connected."""

    _attr_name = "Connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime: GatewayRuntime) -> None:
        super().__init__(runtime, "connection")
        self._update_value()

    def _update_value(self) -> None:
        """Copy the current stream connection state."""
        self._attr_is_on = self._runtime.connected
