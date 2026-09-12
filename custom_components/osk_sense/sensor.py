"""Sensor entities for OSK Sense nodes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final, cast

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfPressure,
    UnitOfTemperature,
)

from .const import (
    CONF_DEVICE_CLASS,
    CONF_PULSE_COUNTERS,
    CONF_UNIT,
    CONF_UNITS_PER_PULSE,
)
from .entity import OskSenseEntity, OskSenseGatewayEntity
from .protocol import ProtocolManifest

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .api import NodeInfo
    from .runtime import GatewayRuntime


@dataclass(frozen=True, kw_only=True)
class OskSensorDescription(SensorEntityDescription):
    """Describe one OSK Sense numeric or timestamp field."""

    source: str = "telemetry"
    units_per_pulse: Decimal | None = None


SENSOR_DESCRIPTIONS: Final = {
    "supply_voltage": OskSensorDescription(
        key="supply_voltage",
        name="Supply voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    "temperature": OskSensorDescription(
        key="temperature",
        name="Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    "humidity": OskSensorDescription(
        key="humidity",
        name="Humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    "pressure": OskSensorDescription(
        key="pressure",
        name="Pressure",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.HPA,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    "count": OskSensorDescription(
        key="count",
        name="Pulse count",
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    "rssi": OskSensorDescription(
        key="rssi",
        name="Signal strength",
        source="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    "received_at": OskSensorDescription(
        key="received_at",
        name="Last telemetry",
        source="received_at",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
}

GATEWAY_SENSOR_DESCRIPTIONS: Final = (
    OskSensorDescription(
        key="active_nodes",
        name="Active nodes",
        source="active_nodes",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    OskSensorDescription(
        key="last_restart",
        name="Last restart",
        source="last_restart",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    OskSensorDescription(
        key="last_stream_message",
        name="Last stream message",
        source="last_stream_message",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    OskSensorDescription(
        key="reconnect_count",
        name="Reconnect count",
        source="reconnect_count",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[GatewayRuntime],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors and discover nodes added to the live registry."""
    runtime = entry.runtime_data
    manifest = runtime.manifest
    known: set[tuple[str, str]] = set()

    async_add_entities(
        OskSenseGatewaySensor(runtime, description)
        for description in GATEWAY_SENSOR_DESCRIPTIONS
    )

    def add_new_entities() -> None:
        entities: list[OskSenseSensor] = []
        for node in runtime.registry.nodes:
            if node.state != "active":
                continue
            keys = _profile_sensor_keys(manifest, node.profile_id)
            for key in (*keys, "rssi", "received_at"):
                identity = (node.device_uid, key)
                if identity in known:
                    continue
                known.add(identity)
                entities.append(OskSenseSensor(runtime, node, SENSOR_DESCRIPTIONS[key]))
            converted = _pulse_counter_description(entry.options, node.device_uid)
            if converted is not None:
                identity = (node.device_uid, converted.key)
                if identity not in known:
                    known.add(identity)
                    entities.append(OskSenseSensor(runtime, node, converted))
        if entities:
            async_add_entities(entities)

    add_new_entities()
    entry.async_on_unload(runtime.async_add_listener(add_new_entities))


def _pulse_counter_description(
    options: Mapping[str, Any], device_uid: str
) -> OskSensorDescription | None:
    """Build the configured physical-total sensor for a pulse counter."""
    counters = options.get(CONF_PULSE_COUNTERS)
    if not isinstance(counters, dict):
        return None
    typed_counters = cast(dict[str, object], counters)
    config = typed_counters.get(device_uid)
    if not isinstance(config, dict):
        return None
    typed_config = cast(dict[str, object], config)
    try:
        factor = Decimal(str(typed_config[CONF_UNITS_PER_PULSE]))
        unit = str(typed_config[CONF_UNIT])
        configured_class = str(typed_config[CONF_DEVICE_CLASS])
        device_class = (
            None if configured_class == "none" else SensorDeviceClass(configured_class)
        )
    except KeyError, ValueError:
        return None
    if not factor.is_finite() or factor <= 0 or not unit:
        return None

    exponent = factor.normalize().as_tuple().exponent
    precision = max(0, min(6, -exponent)) if isinstance(exponent, int) else 0
    return OskSensorDescription(
        key="converted_total",
        name="Total",
        source="converted_total",
        device_class=device_class,
        native_unit_of_measurement=unit,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=precision,
        units_per_pulse=factor,
    )


def _profile_sensor_keys(
    manifest: ProtocolManifest, profile_id: int
) -> tuple[str, ...]:
    profile = manifest.profiles.get(profile_id)
    if profile is None:
        return ()
    fields = [*manifest.telemetry["common_fields"], *profile["fields"]]
    return tuple(
        field["name"]
        for field in fields
        if field.get("quantity") != "binary_state"
        and field["name"] in SENSOR_DESCRIPTIONS
    )


class OskSenseSensor(OskSenseEntity, SensorEntity):
    """Expose one decoded telemetry value."""

    def __init__(
        self,
        runtime: GatewayRuntime,
        node: NodeInfo,
        description: OskSensorDescription,
    ) -> None:
        super().__init__(runtime, node, description.key)
        self.entity_description = description
        self._description = description
        self._update_value()

    def _update_value(self) -> None:
        """Copy the latest telemetry into the native-value attribute."""
        event = self._event
        if event is None:
            self._attr_native_value = None
        elif self._description.source == "rssi":
            self._attr_native_value = event.rssi
        elif self._description.source == "received_at":
            self._attr_native_value = datetime.fromtimestamp(
                event.received_at_unix_ms / 1000, UTC
            )
        elif self._description.source == "converted_total":
            count = event.telemetry.values.get("count")
            self._attr_native_value = (
                Decimal(str(count)) * self._description.units_per_pulse
                if isinstance(count, int | float)
                and self._description.units_per_pulse is not None
                else None
            )
        else:
            value = event.telemetry.values.get(self._description.key)
            self._attr_native_value = (
                value if isinstance(value, int | float | str) else None
            )


class OskSenseGatewaySensor(OskSenseGatewayEntity, SensorEntity):
    """Expose health and status information for the gateway."""

    def __init__(
        self, runtime: GatewayRuntime, description: OskSensorDescription
    ) -> None:
        super().__init__(runtime, description.key)
        self.entity_description = description
        self._description = description
        self._restart_source: tuple[str, int] | None = None
        self._update_value()

    def _update_value(self) -> None:
        """Copy the selected gateway runtime value into native state."""
        if self._description.source == "active_nodes":
            self._attr_native_value = sum(
                node.state == "active" for node in self._runtime.registry.nodes
            )
        elif self._description.source == "last_restart":
            info = self._runtime.bootstrap.info
            source = (info.boot_id, self._runtime.gateway_started_at_unix_ms)
            if source != self._restart_source:
                self._restart_source = source
                self._attr_native_value = datetime.fromtimestamp(
                    self._runtime.gateway_started_at_unix_ms / 1000, UTC
                )
        elif self._description.source == "last_stream_message":
            received_at = self._runtime.last_stream_message_at_unix_ms
            self._attr_native_value = (
                datetime.fromtimestamp(received_at / 1000, UTC)
                if received_at is not None
                else None
            )
        else:
            self._attr_native_value = self._runtime.reconnect_count
