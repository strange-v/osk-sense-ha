"""Tests for persistent gateway runtime state."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from custom_components.osk_sense.api import (
    GatewayBootstrap,
    GatewayInfo,
    GatewayUiInfo,
    NodeInfo,
    NodeRegistry,
)
from custom_components.osk_sense.protocol import DecodedTelemetry
from custom_components.osk_sense.runtime import GatewayRuntime, sequence_is_newer
from custom_components.osk_sense.stream import (
    RegistryUpdatedEvent,
    StreamDisconnectedError,
    StreamSnapshot,
    TelemetryEvent,
)


def _node(uid: str = "102132435465768798A9", *, profile_id: int = 2) -> NodeInfo:
    return NodeInfo(
        7,
        uid,
        "Bedroom",
        profile_id,
        "1.3.0",
        "active",
        False,
        None,
        None,
        2,
        "auto",
        None,
        None,
        None,
        None,
        None,
        None,
    )


def _bootstrap(nodes: tuple[NodeInfo, ...] | None = None) -> GatewayBootstrap:
    info = GatewayInfo(
        "0.8.0",
        1,
        1,
        "0" * 32,
        "1" * 32,
        GatewayUiInfo("ready", "0.1.0", "0.8"),
        "ESP32-S3",
        "osk-hub",
        12345,
    )
    return GatewayBootstrap(info, NodeRegistry(42, nodes or (_node(),)))


def _telemetry(sequence: int, node: NodeInfo | None = None) -> TelemetryEvent:
    return TelemetryEvent(
        sequence,
        node or _node(),
        1_770_000_000_123,
        -71,
        DecodedTelemetry(2, "climate", {}, {"temperature": 23.5}),
    )


class _Stream:
    def __init__(self, snapshot: StreamSnapshot, events: list[object]) -> None:
        self.snapshot = snapshot
        self.events = events
        self.closed = False

    async def async_receive(self):
        if self.events:
            event = self.events.pop(0)
            if isinstance(event, BaseException):
                raise event
            return event
        await asyncio.Future()

    async def async_close(self) -> None:
        self.closed = True


class _BusyStream(_Stream):
    """A stream whose receive never suspends, like a permanently buffered socket."""

    def __init__(self, snapshot: StreamSnapshot) -> None:
        super().__init__(snapshot, [])
        self.receive_count = 0

    async def async_receive(self):
        self.receive_count += 1
        return _telemetry(self.receive_count + 10)


class GatewayRuntimeTest(unittest.IsolatedAsyncioTestCase):
    def test_gateway_uptime_advances_from_rest_snapshot(self) -> None:
        with patch(
            "custom_components.osk_sense.runtime.time.monotonic", return_value=100.0
        ):
            runtime = GatewayRuntime(AsyncMock(), _bootstrap())
        with patch(
            "custom_components.osk_sense.runtime.time.monotonic", return_value=105.9
        ):
            self.assertEqual(12350, runtime.gateway_uptime_seconds)

    def test_wrapping_sequence_comparison(self) -> None:
        self.assertTrue(sequence_is_newer(11, 10))
        self.assertFalse(sequence_is_newer(10, 10))
        self.assertFalse(sequence_is_newer(9, 10))
        self.assertTrue(sequence_is_newer(0, 0xFFFFFFFF))
        self.assertFalse(sequence_is_newer(0xFFFFFFFF, 0))

    async def test_snapshot_live_dedup_registry_and_disconnect(self) -> None:
        node = _node()
        removed = _node("AAAAAAAAAAAAAAAAAAAA")
        snapshot = StreamSnapshot(NodeRegistry(42, (node, removed)), (_telemetry(10),))
        stream = _Stream(
            snapshot,
            [
                _telemetry(10),
                _telemetry(9),
                _telemetry(11),
                RegistryUpdatedEvent(NodeRegistry(43, (removed,))),
                StreamDisconnectedError("closed"),
            ],
        )
        client = AsyncMock()
        client.async_open_stream.return_value = stream
        sleeps: list[float] = []

        async def sleep(delay: float) -> None:
            sleeps.append(delay)
            raise asyncio.CancelledError

        runtime = GatewayRuntime(client, _bootstrap(), sleep=sleep)
        notifications = 0

        def listener() -> None:
            nonlocal notifications
            notifications += 1

        runtime.async_add_listener(listener)
        with self.assertRaises(asyncio.CancelledError):
            await runtime.async_run()

        self.assertFalse(runtime.connected)
        self.assertEqual({}, runtime.latest)
        self.assertEqual(43, runtime.registry.generation)
        self.assertEqual([1.0], sleeps)
        self.assertEqual(1, runtime.reconnect_count)
        self.assertIsNotNone(runtime.last_stream_message_at_unix_ms)
        # connect, new telemetry, registry, disconnect
        self.assertEqual(4, notifications)
        self.assertTrue(stream.closed)

    def test_registry_profile_change_discards_incompatible_telemetry(self) -> None:
        runtime = GatewayRuntime(AsyncMock(), _bootstrap())
        runtime.latest[_node().device_uid] = _telemetry(10)

        runtime._apply_event(
            RegistryUpdatedEvent(NodeRegistry(43, (_node(profile_id=3),)))
        )

        self.assertEqual({}, runtime.latest)

    async def test_buffered_stream_does_not_starve_event_loop(self) -> None:
        node = _node()
        stream = _BusyStream(StreamSnapshot(NodeRegistry(42, (node,)), ()))
        client = AsyncMock()
        client.async_open_stream.return_value = stream
        runtime = GatewayRuntime(client, _bootstrap())

        task = asyncio.create_task(runtime.async_run())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await runtime.async_stop()
        await asyncio.wait_for(task, 1)

        self.assertGreater(stream.receive_count, 0)
        self.assertTrue(stream.closed)


if __name__ == "__main__":
    unittest.main()
