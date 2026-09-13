"""Home Assistant lifecycle tests for the OSK Sense integration."""

from __future__ import annotations

import asyncio
import time
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

try:
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.const import (
        CONF_HOST,
        EVENT_HOMEASSISTANT_STARTED,
        STATE_UNAVAILABLE,
    )
    from homeassistant.core import CoreState
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
except ModuleNotFoundError as error:
    raise unittest.SkipTest("requires a Home Assistant test environment") from error

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.osk_sense.api import (
    GatewayBootstrap,
    GatewayInfo,
    GatewayUiInfo,
    NodeInfo,
    NodeRegistry,
)
from custom_components.osk_sense.const import CONF_TOKEN, DOMAIN
from custom_components.osk_sense.protocol import DecodedTelemetry
from custom_components.osk_sense.runtime import GatewayRuntime
from custom_components.osk_sense.stream import RegistryUpdatedEvent, TelemetryEvent

DEVICE_UID = "102132435465768798A9"
PENDING_UID = "102132435465768798AA"
DISABLED_UID = "102132435465768798AB"
STALE_UID = "102132435465768798AC"

NODE = NodeInfo(
    node_id=7,
    device_uid=DEVICE_UID,
    display_name="Bedroom",
    profile_id=2,
    firmware="1.3.0",
    state="active",
    has_telemetry=False,
    last_seen_at_ms=None,
    rssi=None,
)

BOOTSTRAP = GatewayBootstrap(
    info=GatewayInfo(
        firmware_version="0.8.0",
        api_version=1,
        stream_version=1,
        gateway_id="cccd7e8a5e2bd5d8b9cb754240a82fd8",
        boot_id="1d52f8108f3098bdcc0e1ac5fc71be4b",
        ui=GatewayUiInfo("ready", "0.1.0", "0.8"),
        board="ESP32-S3",
        hostname="osk-hub-test",
        uptime_seconds=12345,
    ),
    registry=NodeRegistry(1, (NODE,)),
)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_entry_lifecycle_entities_and_deferred_stream(hass) -> None:
    """Exercise setup, push update, deferred stream start, and unload."""
    hass.set_state(CoreState.starting)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=BOOTSTRAP.info.gateway_id,
        title=BOOTSTRAP.info.hostname,
        data={CONF_HOST: "http://osk-hub.local", CONF_TOKEN: "secret"},
    )
    entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    stale_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, STALE_UID)},
        name="Removed node",
    )
    stale_entity = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{STALE_UID}_temperature",
        config_entry=entry,
        device_id=stale_device.id,
        original_name="Temperature",
    )

    def get_node_device(device_uid: str):
        return device_registry.async_get_device_by_identifier(
            (DOMAIN, device_uid), entry.entry_id
        )

    stream_started = asyncio.Event()

    async def fake_stream_run(_: GatewayRuntime) -> None:
        stream_started.set()
        await asyncio.Future()

    with (
        patch("custom_components.osk_sense.GatewayApiClient") as client_class,
        patch.object(GatewayRuntime, "async_run", fake_stream_run),
    ):
        client = client_class.return_value
        client.base_url = "http://osk-hub.local"
        client.async_bootstrap = AsyncMock(return_value=BOOTSTRAP)

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        assert not stream_started.is_set()

        temperature_entity_id = entity_registry.async_get_entity_id(
            "sensor", DOMAIN, f"{DEVICE_UID}_temperature"
        )
        voltage_entity_id = entity_registry.async_get_entity_id(
            "sensor", DOMAIN, f"{DEVICE_UID}_supply_voltage"
        )
        gateway_connection_id = entity_registry.async_get_entity_id(
            "binary_sensor", DOMAIN, f"{BOOTSTRAP.info.gateway_id}_connection"
        )
        active_nodes_id = entity_registry.async_get_entity_id(
            "sensor", DOMAIN, f"{BOOTSTRAP.info.gateway_id}_active_nodes"
        )
        assert temperature_entity_id is not None
        assert voltage_entity_id is not None
        assert gateway_connection_id is not None
        assert active_nodes_id is not None
        assert entity_registry.async_get(stale_entity.entity_id) is None
        assert get_node_device(STALE_UID) is None
        assert hass.states.get(temperature_entity_id).state == STATE_UNAVAILABLE
        assert hass.states.get(gateway_connection_id).state == "off"
        assert hass.states.get(active_nodes_id).state == "1"

        runtime = entry.runtime_data
        runtime.connected = True
        runtime.latest[DEVICE_UID] = TelemetryEvent(
            sequence=10,
            node=NODE,
            received_at_unix_ms=int(time.time() * 1000),
            rssi=-71,
            telemetry=DecodedTelemetry(
                profile_id=2,
                profile_name="temperature",
                raw={"supply_voltage": 3303, "temperature": 2350},
                values={"supply_voltage": 3.303, "temperature": 23.5},
            ),
        )
        runtime._notify()  # pyright: ignore[reportPrivateUsage]

        assert hass.states.get(temperature_entity_id).state == "23.5"
        assert hass.states.get(voltage_entity_id).state == "3.303"
        assert hass.states.get(gateway_connection_id).state == "on"

        disabled_node = replace(NODE, state="disabled")
        runtime._apply_event(  # pyright: ignore[reportPrivateUsage]
            RegistryUpdatedEvent(NodeRegistry(2, (disabled_node,)))
        )
        await hass.async_block_till_done()

        assert (
            entity_registry.async_get_entity_id(
                "sensor", DOMAIN, f"{DEVICE_UID}_temperature"
            )
            == temperature_entity_id
        )
        assert get_node_device(DEVICE_UID) is not None
        assert hass.states.get(temperature_entity_id).state == STATE_UNAVAILABLE
        assert hass.states.get(active_nodes_id).state == "0"

        runtime._apply_event(  # pyright: ignore[reportPrivateUsage]
            RegistryUpdatedEvent(NodeRegistry(3, ()))
        )
        await hass.async_block_till_done()

        assert (
            entity_registry.async_get_entity_id(
                "sensor", DOMAIN, f"{DEVICE_UID}_temperature"
            )
            is None
        )
        assert hass.states.get(temperature_entity_id) is None
        assert get_node_device(DEVICE_UID) is None

        pending_node = replace(NODE, device_uid=PENDING_UID, state="pending")
        never_active_disabled_node = replace(
            NODE, device_uid=DISABLED_UID, state="disabled"
        )
        runtime._apply_event(  # pyright: ignore[reportPrivateUsage]
            RegistryUpdatedEvent(
                NodeRegistry(4, (pending_node, never_active_disabled_node))
            )
        )
        await hass.async_block_till_done()

        for device_uid in (PENDING_UID, DISABLED_UID):
            assert (
                entity_registry.async_get_entity_id(
                    "sensor", DOMAIN, f"{device_uid}_temperature"
                )
                is None
            )
            assert get_node_device(device_uid) is None
        assert hass.states.get(active_nodes_id).state == "0"

        runtime._apply_event(  # pyright: ignore[reportPrivateUsage]
            RegistryUpdatedEvent(NodeRegistry(5, (NODE,)))
        )
        await hass.async_block_till_done()

        temperature_entity_id = entity_registry.async_get_entity_id(
            "sensor", DOMAIN, f"{DEVICE_UID}_temperature"
        )
        assert temperature_entity_id is not None
        node_device = get_node_device(DEVICE_UID)
        assert node_device is not None
        assert hass.states.get(active_nodes_id).state == "1"

        binary_node = replace(NODE, profile_id=5)
        runtime._apply_event(  # pyright: ignore[reportPrivateUsage]
            RegistryUpdatedEvent(NodeRegistry(6, (binary_node,)))
        )
        await hass.async_block_till_done()

        assert (
            entity_registry.async_get_entity_id(
                "sensor", DOMAIN, f"{DEVICE_UID}_temperature"
            )
            is None
        )
        state_entity_id = entity_registry.async_get_entity_id(
            "binary_sensor", DOMAIN, f"{DEVICE_UID}_state"
        )
        assert state_entity_id is not None
        updated_node_device = get_node_device(DEVICE_UID)
        assert updated_node_device is not None
        assert updated_node_device.id == node_device.id

        hass.set_state(CoreState.running)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await asyncio.wait_for(stream_started.wait(), 1)

        assert await hass.config_entries.async_unload(entry.entry_id)
        assert entry.state is ConfigEntryState.NOT_LOADED
        assert runtime._stopping  # pyright: ignore[reportPrivateUsage]
