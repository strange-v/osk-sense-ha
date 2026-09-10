"""Tests for the validated OSK Sense WebSocket client."""

from __future__ import annotations

import struct
import unittest
from collections.abc import Awaitable, Callable

import pytest
from aiohttp import ClientSession, web

from custom_components.osk_sense.api import (
    CLIENT_NAME,
    AuthenticationError,
    GatewayApiClient,
    GatewayBootstrap,
    GatewayInfo,
    GatewayUiInfo,
    NodeInfo,
    NodeRegistry,
)
from custom_components.osk_sense.stream import (
    InvalidStreamError,
    RegistryUpdatedEvent,
    StreamResyncRequiredError,
    TelemetryEvent,
)

GATEWAY_ID = "000102030405060708090a0b0c0d0e0f"
BOOT_ID = "101112131415161718191a1b1c1d1e1f"


def _gateway_info() -> GatewayInfo:
    return GatewayInfo(
        firmware_version="0.8.0",
        api_version=1,
        stream_version=1,
        gateway_id=GATEWAY_ID,
        boot_id=BOOT_ID,
        ui=GatewayUiInfo("ready", "0.1.0", "0.8"),
        board="ESP32-S3",
        hostname="osk-hub",
    )


def _node(*, profile_id: int = 2, display_name: str = "Bedroom") -> NodeInfo:
    return NodeInfo(
        node_id=7,
        device_uid="102132435465768798A9",
        display_name=display_name,
        profile_id=profile_id,
        firmware="1.3.0",
        state="active",
        has_telemetry=True,
        last_seen_at_ms=1_770_000_000_000,
        rssi=-74,
    )


def _bootstrap(*, generation: int = 42, profile_id: int = 2) -> GatewayBootstrap:
    return GatewayBootstrap(
        _gateway_info(), NodeRegistry(generation, (_node(profile_id=profile_id),))
    )


def _prefix(kind: int, sequence: int) -> bytes:
    return struct.pack("<BBI", 1, kind, sequence)


def _hello(*, generation: int = 42, gateway_id: str = GATEWAY_ID) -> bytes:
    return (
        _prefix(1, 10)
        + bytes.fromhex(gateway_id)
        + bytes.fromhex(BOOT_ID)
        + struct.pack("<I", generation)
    )


def _snapshot_control(kind: int, generation: int = 42) -> bytes:
    return _prefix(kind, 10) + struct.pack("<I", generation)


def _telemetry(*, profile_id: int = 2, sequence: int = 11) -> bytes:
    payload = bytes.fromhex("40E40C2E09")
    return (
        _prefix(3, sequence)
        + struct.pack("<BHQhB", 7, profile_id, 1_770_000_000_123, -71, len(payload))
        + payload
    )


def _registry_changed(generation: int, *, sequence: int = 12) -> bytes:
    return _prefix(5, sequence) + struct.pack("<I", generation)


WebSocketScenario = Callable[[web.WebSocketResponse], Awaitable[None]]


@pytest.mark.usefixtures("socket_enabled")
class StreamClientTest(unittest.IsolatedAsyncioTestCase):
    """Exercise the stream client against a real local aiohttp server."""

    async def asyncSetUp(self) -> None:
        self.registry = NodeRegistry(42, (_node(),))
        self.seen_authorization: str | None = None
        self.seen_client_name: str | None = None
        self.scenario: WebSocketScenario = self._send_empty_snapshot
        self.reject_websocket = False

        app = web.Application()
        app.router.add_get("/api/nodes", self._handle_nodes)
        app.router.add_get("/ws", self._handle_websocket)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        server = self.site._server
        assert server is not None
        port = server.sockets[0].getsockname()[1]
        self.session = ClientSession()
        self.client = GatewayApiClient(f"127.0.0.1:{port}", "test-token", self.session)

    async def asyncTearDown(self) -> None:
        await self.session.close()
        await self.runner.cleanup()

    async def _handle_nodes(self, _: web.Request) -> web.Response:
        return web.json_response(
            {
                "registry_generation": self.registry.generation,
                "nodes": [
                    {
                        "node_id": node.node_id,
                        "device_uid": node.device_uid,
                        "display_name": node.display_name,
                        "profile_id": node.profile_id,
                        "firmware": node.firmware,
                        "state": node.state,
                        "has_telemetry": node.has_telemetry,
                        "last_seen_at_ms": node.last_seen_at_ms,
                        "rssi": node.rssi,
                    }
                    for node in self.registry.nodes
                ],
            }
        )

    async def _handle_websocket(self, request: web.Request) -> web.StreamResponse:
        self.seen_authorization = request.headers.get("Authorization")
        self.seen_client_name = request.headers.get("X-Client")
        if self.reject_websocket:
            return web.Response(status=401)
        websocket = web.WebSocketResponse()
        await websocket.prepare(request)
        await self.scenario(websocket)
        async for _ in websocket:
            pass
        return websocket

    async def _send_empty_snapshot(self, websocket: web.WebSocketResponse) -> None:
        await websocket.send_bytes(_hello())
        await websocket.send_bytes(_snapshot_control(2))
        await websocket.send_bytes(_snapshot_control(4))

    async def test_snapshot_and_live_registry_update(self) -> None:
        async def scenario(websocket: web.WebSocketResponse) -> None:
            await websocket.send_bytes(_hello())
            await websocket.send_bytes(_snapshot_control(2))
            await websocket.send_bytes(_telemetry())
            await websocket.send_bytes(_snapshot_control(4))
            self.registry = NodeRegistry(43, (_node(display_name="Main bedroom"),))
            await websocket.send_bytes(_registry_changed(43))
            await websocket.send_bytes(_telemetry(sequence=13))

        self.scenario = scenario
        stream = await self.client.async_open_stream(_bootstrap())
        self.addAsyncCleanup(stream.async_close)

        self.assertEqual("Bearer test-token", self.seen_authorization)
        self.assertEqual(CLIENT_NAME, self.seen_client_name)
        self.assertEqual(1, len(stream.snapshot.telemetry))
        first = stream.snapshot.telemetry[0]
        self.assertEqual(23.5, first.telemetry.values["temperature"])
        self.assertEqual(-71, first.rssi)

        registry_event = await stream.async_receive()
        self.assertIsInstance(registry_event, RegistryUpdatedEvent)
        assert isinstance(registry_event, RegistryUpdatedEvent)
        self.assertEqual("Main bedroom", registry_event.registry.nodes[0].display_name)

        live_event = await stream.async_receive()
        self.assertIsInstance(live_event, TelemetryEvent)
        assert isinstance(live_event, TelemetryEvent)
        self.assertEqual(13, live_event.sequence)
        self.assertEqual("Main bedroom", live_event.node.display_name)

    async def test_hello_generation_is_reconciled_before_snapshot(self) -> None:
        self.registry = NodeRegistry(42, (_node(),))
        stream = await self.client.async_open_stream(_bootstrap(generation=41))
        self.addAsyncCleanup(stream.async_close)
        self.assertEqual(42, stream.snapshot.registry.generation)

    async def test_registry_race_requires_reconnect(self) -> None:
        async def scenario(websocket: web.WebSocketResponse) -> None:
            await websocket.send_bytes(_hello(generation=43))
            await websocket.send_bytes(_snapshot_control(2, 43))
            await websocket.send_bytes(_snapshot_control(4, 43))

        self.scenario = scenario
        self.registry = NodeRegistry(44, (_node(),))
        with self.assertRaises(StreamResyncRequiredError):
            await self.client.async_open_stream(_bootstrap())

    async def test_identity_mismatch_is_rejected(self) -> None:
        async def scenario(websocket: web.WebSocketResponse) -> None:
            await websocket.send_bytes(_hello(gateway_id="f" * 32))

        self.scenario = scenario
        with self.assertRaisesRegex(InvalidStreamError, "gateway_id"):
            await self.client.async_open_stream(_bootstrap())

    async def test_unknown_kind_cannot_precede_hello(self) -> None:
        async def scenario(websocket: web.WebSocketResponse) -> None:
            await websocket.send_bytes(_prefix(99, 9))
            await websocket.send_bytes(_hello())

        self.scenario = scenario
        with self.assertRaisesRegex(InvalidStreamError, "HELLO"):
            await self.client.async_open_stream(_bootstrap())

    async def test_profile_mismatch_is_rejected(self) -> None:
        async def scenario(websocket: web.WebSocketResponse) -> None:
            await websocket.send_bytes(_hello())
            await websocket.send_bytes(_snapshot_control(2))
            await websocket.send_bytes(_telemetry(profile_id=3))

        self.scenario = scenario
        with self.assertRaisesRegex(InvalidStreamError, "profile"):
            await self.client.async_open_stream(_bootstrap())

    async def test_websocket_authentication_error(self) -> None:
        self.reject_websocket = True
        with self.assertRaises(AuthenticationError):
            await self.client.async_open_stream(_bootstrap())


if __name__ == "__main__":
    unittest.main()
