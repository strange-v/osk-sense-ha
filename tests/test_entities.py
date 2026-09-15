"""Entity mapping tests."""

from __future__ import annotations

import time
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from homeassistant.components.sensor import SensorDeviceClass

from custom_components.osk_sense.api import (
    GatewayBootstrap,
    GatewayInfo,
    GatewayUiInfo,
    NodeInfo,
    NodeRegistry,
)
from custom_components.osk_sense.binary_sensor import (
    BINARY_SENSOR_DESCRIPTIONS,
    OskSenseBinarySensor,
    OskSenseGatewayConnection,
    _profile_binary_keys,  # pyright: ignore[reportPrivateUsage]
)
from custom_components.osk_sense.protocol import DecodedTelemetry, ProtocolManifest
from custom_components.osk_sense.runtime import GatewayRuntime
from custom_components.osk_sense.sensor import (
    GATEWAY_SENSOR_DESCRIPTIONS,
    SENSOR_DESCRIPTIONS,
    OskSenseGatewaySensor,
    OskSenseSensor,
    _profile_sensor_keys,  # pyright: ignore[reportPrivateUsage]
    _pulse_counter_description,  # pyright: ignore[reportPrivateUsage]
)
from custom_components.osk_sense.stream import TelemetryEvent


def _runtime(
    profile_id: int,
    values: dict[str, int | float],
    *,
    uptime_seconds: int = 12345,
) -> GatewayRuntime:
    node = NodeInfo(
        7,
        "102132435465768798A9",
        "Bedroom",
        profile_id,
        "1.3.0",
        "active",
        True,
        1_770_000_000_000,
        -71,
        2,
        "auto",
        None,
        2,
        2,
        False,
        False,
        -70,
    )
    info = GatewayInfo(
        "0.8.0",
        1,
        1,
        "0" * 32,
        "1" * 32,
        GatewayUiInfo("ready", "0.1.0", "0.8"),
        "ESP32-S3",
        "osk-hub",
        uptime_seconds,
    )
    runtime = GatewayRuntime(
        AsyncMock(), GatewayBootstrap(info, NodeRegistry(1, (node,)))
    )
    runtime.connected = True
    decoded_values: dict[str, int | float | bytes | str | None] = {}
    decoded_values.update(values)
    runtime.latest[node.device_uid] = TelemetryEvent(
        10,
        node,
        int(time.time() * 1000),
        -71,
        DecodedTelemetry(profile_id, "test", {}, decoded_values),
    )
    return runtime


class EntityMappingTest(unittest.TestCase):
    def test_manifest_fields_are_assigned_to_platforms(self) -> None:
        manifest = ProtocolManifest.load_default()
        self.assertEqual(
            (
                "tx_power_level",
                "downlink_rssi",
                "supply_voltage",
                "temperature",
                "humidity",
                "pressure",
            ),
            _profile_sensor_keys(manifest, 4),
        )
        self.assertEqual(
            (
                "tx_power_level",
                "downlink_rssi",
                "supply_voltage",
                "temperature",
                "humidity",
            ),
            _profile_sensor_keys(manifest, 7),
        )
        self.assertEqual(("radio_fallback", "state"), _profile_binary_keys(manifest, 7))
        self.assertEqual(("radio_fallback",), _profile_binary_keys(manifest, 4))

    def test_sensor_value_identity_and_availability(self) -> None:
        runtime = _runtime(2, {"supply_voltage": 3.2, "temperature": 23.5})
        node = runtime.registry.nodes[0]
        entity = OskSenseSensor(runtime, node, SENSOR_DESCRIPTIONS["temperature"])

        self.assertEqual("102132435465768798A9_temperature", entity.unique_id)
        self.assertEqual(23.5, entity.native_value)
        self.assertEqual(1, entity.suggested_display_precision)
        self.assertTrue(entity.available)

        runtime.connected = False
        self.assertFalse(
            entity._calculate_available()  # pyright: ignore[reportPrivateUsage]
        )

    def test_voltage_keeps_value_and_suggests_two_decimal_places(self) -> None:
        runtime = _runtime(1, {"supply_voltage": 3.303})
        node = runtime.registry.nodes[0]
        entity = OskSenseSensor(runtime, node, SENSOR_DESCRIPTIONS["supply_voltage"])

        self.assertEqual(3.303, entity.native_value)
        self.assertEqual(2, entity.suggested_display_precision)

    def test_humidity_is_displayed_as_whole_percent(self) -> None:
        description = SENSOR_DESCRIPTIONS["humidity"]
        self.assertEqual(0, description.suggested_display_precision)

    def test_radio_diagnostics_map_reported_values(self) -> None:
        runtime = _runtime(
            2,
            {
                "tx_power_level": 2,
                "radio_fallback": 1,
                "downlink_rssi": -70,
            },
        )
        node = runtime.registry.nodes[0]
        power = OskSenseSensor(runtime, node, SENSOR_DESCRIPTIONS["tx_power_level"])
        downlink = OskSenseSensor(runtime, node, SENSOR_DESCRIPTIONS["downlink_rssi"])
        fallback = OskSenseBinarySensor(
            runtime, node, BINARY_SENSOR_DESCRIPTIONS["radio_fallback"]
        )

        self.assertEqual(2, power.native_value)
        self.assertEqual(0, power.suggested_display_precision)
        self.assertEqual(-70, downlink.native_value)
        self.assertEqual(SensorDeviceClass.SIGNAL_STRENGTH, downlink.device_class)
        self.assertIs(fallback.is_on, True)
        self.assertFalse(power.entity_registry_enabled_default)
        self.assertFalse(downlink.entity_registry_enabled_default)
        self.assertFalse(fallback.entity_registry_enabled_default)

    def test_gateway_health_entities(self) -> None:
        runtime = _runtime(2, {"temperature": 23.5}, uptime_seconds=3600)
        runtime.last_stream_message_at_unix_ms = 1_770_000_000_123
        runtime.reconnect_count = 3
        descriptions = {
            description.key: description for description in GATEWAY_SENSOR_DESCRIPTIONS
        }

        active_nodes = OskSenseGatewaySensor(runtime, descriptions["active_nodes"])
        last_restart = OskSenseGatewaySensor(runtime, descriptions["last_restart"])
        last_message = OskSenseGatewaySensor(
            runtime, descriptions["last_stream_message"]
        )
        reconnects = OskSenseGatewaySensor(runtime, descriptions["reconnect_count"])
        connection = OskSenseGatewayConnection(runtime)

        self.assertEqual(1, active_nodes.native_value)
        assert isinstance(last_restart.native_value, datetime)
        self.assertAlmostEqual(
            3600,
            (datetime.now(UTC) - last_restart.native_value).total_seconds(),
            delta=1,
        )
        self.assertEqual(
            datetime.fromtimestamp(1_770_000_000.123, UTC),
            last_message.native_value,
        )
        self.assertEqual(3, reconnects.native_value)
        self.assertIs(connection.is_on, True)
        self.assertEqual(
            f"{runtime.bootstrap.info.gateway_id}_connection", connection.unique_id
        )

    def test_pulse_counter_can_expose_converted_nonmetric_total(self) -> None:
        runtime = _runtime(6, {"count": 123})
        node = runtime.registry.nodes[0]
        description = _pulse_counter_description(
            {
                "pulse_counters": {
                    node.device_uid: {
                        "units_per_pulse": 0.01,
                        "unit": "gal",
                        "device_class": "water",
                    }
                }
            },
            node.device_uid,
        )

        self.assertIsNotNone(description)
        assert description is not None
        entity = OskSenseSensor(runtime, node, description)
        self.assertEqual(Decimal("1.23"), entity.native_value)
        self.assertEqual("gal", entity.native_unit_of_measurement)
        self.assertEqual(SensorDeviceClass.WATER, entity.device_class)
        self.assertEqual(2, entity.suggested_display_precision)

    def test_pulse_counter_custom_unit_has_no_device_class(self) -> None:
        description = _pulse_counter_description(
            {
                "pulse_counters": {
                    "counter": {
                        "units_per_pulse": 2.5,
                        "unit": "items",
                        "device_class": "none",
                    }
                }
            },
            "counter",
        )

        self.assertIsNotNone(description)
        assert description is not None
        self.assertIsNone(description.device_class)

    def test_no_snapshot_telemetry_is_unknown_but_available(self) -> None:
        runtime = _runtime(2, {})
        runtime.latest.clear()
        node = runtime.registry.nodes[0]
        entity = OskSenseSensor(runtime, node, SENSOR_DESCRIPTIONS["temperature"])

        self.assertIsNone(entity.native_value)
        self.assertTrue(entity.available)

    def test_stale_telemetry_is_unavailable(self) -> None:
        runtime = _runtime(5, {"state": 1})
        node = runtime.registry.nodes[0]
        entity = OskSenseBinarySensor(
            runtime, node, BINARY_SENSOR_DESCRIPTIONS["state"]
        )
        event = runtime.latest[node.device_uid]

        with patch(
            "custom_components.osk_sense.entity.time.time",
            return_value=event.received_at_unix_ms / 1000 + 8101,
        ):
            self.assertFalse(
                entity._calculate_available()  # pyright: ignore[reportPrivateUsage]
            )
        self.assertIs(entity.is_on, True)


if __name__ == "__main__":
    unittest.main()
