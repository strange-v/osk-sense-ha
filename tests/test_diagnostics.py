"""Tests for OSK Sense diagnostics."""

from __future__ import annotations

import json
import time
import unittest
from unittest.mock import AsyncMock

try:
    from homeassistant.components.diagnostics import REDACTED
    from homeassistant.const import CONF_HOST
except ModuleNotFoundError as error:
    raise unittest.SkipTest("requires a Home Assistant test environment") from error

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.osk_sense.api import (
    GatewayBootstrap,
    GatewayInfo,
    GatewayUiInfo,
    NodeInfo,
    NodeRegistry,
)
from custom_components.osk_sense.const import CONF_TOKEN, DOMAIN
from custom_components.osk_sense.diagnostics import async_get_config_entry_diagnostics
from custom_components.osk_sense.protocol import DecodedTelemetry
from custom_components.osk_sense.runtime import GatewayRuntime
from custom_components.osk_sense.stream import TelemetryEvent

DEVICE_UID = "102132435465768798A9"


async def test_config_entry_diagnostics_are_useful_and_redacted(hass) -> None:
    """Test diagnostics preserve runtime details without leaking identifiers."""
    node = NodeInfo(
        7,
        DEVICE_UID,
        "Basement water meter",
        6,
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
        "private-hub.local",
        12345,
    )
    runtime = GatewayRuntime(
        AsyncMock(), GatewayBootstrap(info, NodeRegistry(42, (node,)))
    )
    runtime.connected = True
    runtime.latest[DEVICE_UID] = TelemetryEvent(
        10,
        node,
        int(time.time() * 1000),
        -71,
        DecodedTelemetry(6, "pulse_counter", {"payload": b"\x01\x02"}, {"count": 123}),
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "http://192.0.2.10", CONF_TOKEN: "super-secret"},
        options={
            "pulse_counters": {
                DEVICE_UID: {
                    "units_per_pulse": 0.01,
                    "unit": "gal",
                    "device_class": "water",
                }
            }
        },
    )
    entry.runtime_data = runtime

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(diagnostics)

    assert diagnostics["config_entry"]["data"] == {
        CONF_HOST: REDACTED,
        CONF_TOKEN: REDACTED,
    }
    assert diagnostics["runtime"] == {
        "connected": True,
        "registry_generation": 42,
        "node_count": 1,
        "telemetry_count": 1,
        "last_stream_message_at_unix_ms": None,
        "reconnect_count": 0,
    }
    assert diagnostics["nodes"][0]["identifier"] == "node_1"
    assert diagnostics["nodes"][0]["telemetry"]["values"] == {"count": 123}
    assert diagnostics["nodes"][0]["telemetry"]["raw"] == {"payload": "0102"}
    assert diagnostics["config_entry"]["options"]["pulse_counters"] == {
        "node_1": {
            "units_per_pulse": 0.01,
            "unit": "gal",
            "device_class": "water",
        }
    }
    for secret in (
        "super-secret",
        "192.0.2.10",
        DEVICE_UID,
        "Basement water meter",
        "private-hub.local",
        "0" * 32,
        "1" * 32,
    ):
        assert secret not in serialized
