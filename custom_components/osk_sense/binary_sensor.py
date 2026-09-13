"""Binary sensor entities for OSK Sense nodes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
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


BINARY_SENSOR_DESCRIPTIONS: Final = {
    "state": BinarySensorEntityDescription(
        key="state",
        name="State",
    ),
    "radio_fallback": BinarySensorEntityDescription(
        key="radio_fallback",
        name="Radio fallback",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[GatewayRuntime],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary-state fields and discover newly registered nodes."""
    runtime = entry.runtime_data
    manifest = runtime.manifest
    tracked: dict[tuple[str, str], OskSenseBinarySensor] = {}

    async_add_entities([OskSenseGatewayConnection(runtime)])

    def reconcile_entities() -> None:
        nodes_by_uid = {node.device_uid: node for node in runtime.registry.nodes}
        desired: dict[
            tuple[str, str], tuple[NodeInfo, BinarySensorEntityDescription]
        ] = {}
        for node in runtime.registry.nodes:
            if node.state != "active":
                continue
            for key in _profile_binary_keys(manifest, node.profile_id):
                desired[(node.device_uid, key)] = (
                    node,
                    BINARY_SENSOR_DESCRIPTIONS[key],
                )
        entity_registry = er.async_get(hass)
        for identity, entity in tuple(tracked.items()):
            node = nodes_by_uid.get(identity[0])
            if node is not None and (node.state != "active" or identity in desired):
                continue
            tracked.pop(identity)
            if entity_registry.async_get(entity.entity_id) is not None:
                entity_registry.async_remove(entity.entity_id)

        entities: list[OskSenseBinarySensor] = []
        for identity, (node, description) in desired.items():
            if identity in tracked:
                continue
            entity = OskSenseBinarySensor(runtime, node, description)
            tracked[identity] = entity
            entities.append(entity)
        if entities:
            async_add_entities(entities)

    reconcile_entities()
    entry.async_on_unload(runtime.async_add_listener(reconcile_entities))


def _profile_binary_keys(
    manifest: ProtocolManifest, profile_id: int
) -> tuple[str, ...]:
    """Return binary fields represented by one telemetry profile."""
    profile = manifest.profiles.get(profile_id)
    if profile is None:
        return ()
    fields = [*manifest.telemetry["common_fields"], *profile["fields"]]
    return tuple(
        logical_field["name"]
        for field in fields
        for logical_field in field.get("bits", (field,))
        if logical_field.get("quantity") == "binary_state"
        and logical_field["name"] in BINARY_SENSOR_DESCRIPTIONS
    )


class OskSenseBinarySensor(OskSenseEntity, BinarySensorEntity):
    """Expose a decoded binary-state field."""

    def __init__(
        self,
        runtime: GatewayRuntime,
        node: NodeInfo,
        description: BinarySensorEntityDescription,
    ) -> None:
        super().__init__(runtime, node, description.key)
        self.entity_description = description
        self._key = description.key
        self._update_value()

    def _update_value(self) -> None:
        """Copy the latest binary state into its HA state attribute."""
        event = self._event
        if event is None or (value := event.telemetry.values.get(self._key)) is None:
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
