"""Tests for the async OSK Sense REST client."""

from __future__ import annotations

import socket
import unittest
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import ClientSession, web

from custom_components.osk_sense.api import (
    WEBSOCKET_HEARTBEAT_SECONDS,
    ApiResponseError,
    AuthenticationError,
    CannotConnectError,
    GatewayApiClient,
    GatewayBootstrap,
    InvalidResponseError,
    UnsupportedVersionError,
    normalize_base_url,
)

INFO = {
    "firmware_version": "0.8.0",
    "api_version": 1,
    "stream_version": 1,
    "gateway_id": "cccd7e8a5e2bd5d8b9cb754240a82fd8",
    "boot_id": "1d52f8108f3098bdcc0e1ac5fc71be4b",
    "ui": {"state": "ready", "version": "0.1.0", "required_firmware": "0.8"},
    "board": "Waveshare ESP32-S3-ETH + PoE",
    "hostname": "osk-hub-a085e3e6cc20",
    "uptime_seconds": 12345,
}

NODES = {
    "registry_generation": 12,
    "nodes": [
        {
            "node_id": 7,
            "device_uid": "102132435465768798A9",
            "display_name": "Bedroom",
            "profile_id": 2,
            "firmware": "1.3.0",
            "state": "active",
            "max_power_level": 2,
            "power_policy": "auto",
            "tx_power_target": 2,
            "has_telemetry": True,
            "last_seen_at_ms": 1_770_000_000_000,
            "rssi": -74,
            "tx_power_level": 2,
            "radio_fallback": False,
            "supply_limited": False,
            "downlink_rssi": -71,
        },
        {
            "node_id": 8,
            "device_uid": "AABBCCDDEEFF00112233",
            "display_name": "",
            "profile_id": 5,
            "firmware": "1.3.0",
            "state": "future_state",
            "max_power_level": 2,
            "power_policy": "fixed",
            "fixed_power_level": 1,
            "has_telemetry": False,
        },
    ],
}


@pytest.mark.usefixtures("socket_enabled")
class ApiClientTest(unittest.IsolatedAsyncioTestCase):
    """Exercise the client against a real local aiohttp server."""

    async def asyncSetUp(self) -> None:
        self.info = deepcopy(INFO)
        self.nodes = deepcopy(NODES)
        self.info_status = 200
        self.info_raw_body: str | None = None
        self.nodes_status = 200
        self.nodes_content_type = "application/json"
        self.nodes_raw_body: str | None = None
        self.seen_info_authorization: str | None = None
        self.seen_nodes_authorization: str | None = None
        app = web.Application()
        app.router.add_get("/api/info", self._handle_info)
        app.router.add_get("/api/nodes", self._handle_nodes)
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

    async def _handle_info(self, request: web.Request) -> web.Response:
        self.seen_info_authorization = request.headers.get("Authorization")
        if self.info_raw_body is not None:
            return web.Response(
                text=self.info_raw_body,
                status=self.info_status,
                content_type="application/json",
            )
        return web.json_response(self.info, status=self.info_status)

    async def _handle_nodes(self, request: web.Request) -> web.Response:
        self.seen_nodes_authorization = request.headers.get("Authorization")
        if self.nodes_raw_body is not None:
            return web.Response(
                text=self.nodes_raw_body,
                status=self.nodes_status,
                content_type=self.nodes_content_type,
            )
        if self.nodes_content_type == "application/json":
            return web.json_response(self.nodes, status=self.nodes_status)
        return web.Response(
            text=str(self.nodes),
            status=self.nodes_status,
            content_type=self.nodes_content_type,
        )

    async def test_bootstrap_reads_info_then_authenticated_nodes(self) -> None:
        result = await self.client.async_bootstrap()
        self.assertIsInstance(result, GatewayBootstrap)
        self.assertEqual(INFO["gateway_id"], result.info.gateway_id)
        self.assertEqual(12, result.registry.generation)
        self.assertEqual(2, len(result.registry.nodes))
        self.assertEqual(2, result.registry.nodes[0].max_power_level)
        self.assertEqual("auto", result.registry.nodes[0].power_policy)
        self.assertEqual(2, result.registry.nodes[0].tx_power_level)
        self.assertEqual(-71, result.registry.nodes[0].downlink_rssi)
        self.assertEqual("future_state", result.registry.nodes[1].state)
        self.assertEqual(1, result.registry.nodes[1].fixed_power_level)
        self.assertIsNone(self.seen_info_authorization)
        self.assertEqual("Bearer test-token", self.seen_nodes_authorization)

    async def test_authentication_error(self) -> None:
        self.nodes_status = 401
        with self.assertRaises(AuthenticationError):
            await self.client.async_get_nodes()

    async def test_info_unauthorized_is_an_api_error_not_invalid_token(self) -> None:
        self.info_status = 401
        with self.assertRaises(ApiResponseError):
            await self.client.async_get_info()

    async def test_api_error_preserves_machine_code(self) -> None:
        self.nodes_status = 409
        self.nodes = {"error": "registry_busy"}
        with self.assertRaises(ApiResponseError) as context:
            await self.client.async_get_nodes()
        self.assertEqual(409, context.exception.status)
        self.assertEqual("registry_busy", context.exception.error_code)

    async def test_unsupported_versions_stop_before_authenticated_request(self) -> None:
        self.info["stream_version"] = 2
        with self.assertRaises(UnsupportedVersionError) as context:
            await self.client.async_bootstrap()
        self.assertEqual(1, context.exception.api_version)
        self.assertEqual(2, context.exception.stream_version)
        self.assertIsNone(self.seen_nodes_authorization)

    async def test_non_json_response_is_invalid(self) -> None:
        self.nodes_content_type = "text/plain"
        with self.assertRaisesRegex(InvalidResponseError, "response is not JSON"):
            await self.client.async_get_nodes()

    async def test_malformed_json_response_is_invalid(self) -> None:
        self.nodes_raw_body = "{not-json"
        with self.assertRaisesRegex(InvalidResponseError, "invalid JSON"):
            await self.client.async_get_nodes()

    async def test_invalid_gateway_identity_is_rejected(self) -> None:
        self.info["gateway_id"] = "NOT-A-GATEWAY-ID"
        with self.assertRaisesRegex(InvalidResponseError, "gateway_id"):
            await self.client.async_get_info()

    async def test_gateway_uptime_is_validated(self) -> None:
        """Accept the required unsigned gateway uptime."""
        self.info["uptime_seconds"] = 4_000_000_000
        new_info = await self.client.async_get_info()
        self.assertEqual(4_000_000_000, new_info.uptime_seconds)

        for invalid in (None, -1, True, "12345"):
            with self.subTest(invalid):
                self.info["uptime_seconds"] = invalid
                with self.assertRaisesRegex(InvalidResponseError, "uptime_seconds"):
                    await self.client.async_get_info()

    async def test_inconsistent_telemetry_metadata_is_rejected(self) -> None:
        self.nodes["nodes"][1]["rssi"] = -70
        with self.assertRaisesRegex(InvalidResponseError, "must be absent"):
            await self.client.async_get_nodes()

    async def test_radio_power_metadata_is_validated(self) -> None:
        node = self.nodes["nodes"][0]
        node["tx_power_level"] = 3
        with self.assertRaisesRegex(InvalidResponseError, "ceiling"):
            await self.client.async_get_nodes()

        node["tx_power_level"] = 2
        node["power_policy"] = "fixed"
        with self.assertRaisesRegex(InvalidResponseError, "fixed_power_level"):
            await self.client.async_get_nodes()

    async def test_unknown_node_state_is_additive(self) -> None:
        registry = await self.client.async_get_nodes()
        self.assertEqual("future_state", registry.nodes[1].state)

    async def test_connection_failure_is_distinct(self) -> None:
        with socket.socket() as temporary_socket:
            temporary_socket.bind(("127.0.0.1", 0))
            port = temporary_socket.getsockname()[1]
        client = GatewayApiClient(
            f"127.0.0.1:{port}", "token", self.session, timeout=0.2
        )
        with self.assertRaises(CannotConnectError):
            await client.async_get_info()

    async def test_websocket_uses_client_heartbeat(self) -> None:
        """Ensure a silent broken connection is detected without server traffic."""
        websocket = AsyncMock()
        with patch.object(
            self.session,
            "ws_connect",
            AsyncMock(return_value=websocket),
        ) as connect:
            result = await self.client._async_connect_websocket()  # pyright: ignore[reportPrivateUsage]

        self.assertIs(result, websocket)
        self.assertEqual(
            WEBSOCKET_HEARTBEAT_SECONDS,
            connect.await_args.kwargs["heartbeat"],
        )


class NormalizeBaseUrlTest(unittest.TestCase):
    """Validate host normalization without network access."""

    def test_normalizes_dns_ipv4_and_ipv6_hosts(self) -> None:
        self.assertEqual("http://osk-hub.local", normalize_base_url("osk-hub.local/"))
        self.assertEqual("http://192.0.2.4:8080", normalize_base_url("192.0.2.4:8080"))
        self.assertEqual("http://[2001:db8::1]", normalize_base_url("[2001:db8::1]"))

    def test_rejects_non_http_and_url_components(self) -> None:
        for value in (
            "https://osk-hub.local",
            "http://user@osk-hub.local",
            "osk-hub.local/api",
            "osk-hub.local?query=yes",
            "osk-hub.local#fragment",
        ):
            with self.subTest(value):
                with self.assertRaises(ValueError):
                    normalize_base_url(value)


if __name__ == "__main__":
    unittest.main()
