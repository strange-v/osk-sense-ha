"""Async REST client for the OSK Sense Hub external API."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, TypeGuard, cast

from aiohttp import (
    ClientError,
    ClientSession,
    ClientTimeout,
    ClientWebSocketResponse,
    WSServerHandshakeError,
)
from yarl import URL

if TYPE_CHECKING:
    from .protocol import ProtocolManifest
    from .stream import GatewayStream

JsonObject = dict[str, Any]

DEFAULT_TIMEOUT: Final = 10.0
MAX_RESPONSE_SIZE: Final = 1024 * 1024
MAX_STREAM_MESSAGE_SIZE: Final = 1024
SUPPORTED_API_VERSIONS: Final = frozenset({1})
SUPPORTED_STREAM_VERSIONS: Final = frozenset({1})
CLIENT_NAME: Final = "home-assistant/0.1.0"

_LOWER_HEX_128 = re.compile(r"^[0-9a-f]{32}$")
_UPPER_HEX_UID = re.compile(r"^[0-9A-F]{20}$")
_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


class GatewayApiError(Exception):
    """Base exception for the OSK Sense external API client."""


class CannotConnectError(GatewayApiError):
    """The gateway could not be reached before the request timed out."""


class AuthenticationError(GatewayApiError):
    """The bearer token was rejected by the gateway."""


class InvalidResponseError(GatewayApiError):
    """The gateway response does not match the external API contract."""


class ApiResponseError(GatewayApiError):
    """The gateway returned a non-success HTTP response."""

    def __init__(self, status: int, error_code: str | None = None) -> None:
        self.status = status
        self.error_code = error_code
        detail = f"HTTP {status}"
        if error_code is not None:
            detail = f"{detail}: {error_code}"
        super().__init__(detail)


class UnsupportedVersionError(GatewayApiError):
    """The gateway exposes an API or stream version this client cannot use."""

    def __init__(self, api_version: int, stream_version: int) -> None:
        self.api_version = api_version
        self.stream_version = stream_version
        super().__init__(
            f"unsupported API/stream versions: {api_version}/{stream_version}"
        )


@dataclass(frozen=True, slots=True)
class GatewayUiInfo:
    """Web UI compatibility state reported by the gateway."""

    state: str
    version: str
    required_firmware: str


@dataclass(frozen=True, slots=True)
class GatewayInfo:
    """Stable gateway identity and external contract versions."""

    firmware_version: str
    api_version: int
    stream_version: int
    gateway_id: str
    boot_id: str
    ui: GatewayUiInfo
    board: str
    hostname: str


@dataclass(frozen=True, slots=True)
class NodeInfo:
    """One node registry record."""

    node_id: int
    device_uid: str
    display_name: str
    profile_id: int
    firmware: str
    state: str
    has_telemetry: bool
    last_seen_at_ms: int | None
    rssi: int | None


@dataclass(frozen=True, slots=True)
class NodeRegistry:
    """A generation-consistent snapshot of the gateway node registry."""

    generation: int
    nodes: tuple[NodeInfo, ...]


@dataclass(frozen=True, slots=True)
class GatewayBootstrap:
    """The REST state required before opening the telemetry stream."""

    info: GatewayInfo
    registry: NodeRegistry


def normalize_base_url(host: str) -> str:
    """Normalize a user-supplied host into an HTTP API base URL."""
    value = host.strip()
    if not value:
        raise ValueError("host is required")
    if "://" not in value:
        value = f"http://{value}"
    try:
        url = URL(value)
        port = url.port
    except (TypeError, ValueError) as error:
        raise ValueError("invalid host") from error
    if url.scheme != "http":
        raise ValueError("only http is supported")
    if url.host is None or url.user is not None or url.password is not None:
        raise ValueError("invalid host")
    if url.path not in ("", "/") or url.query_string or url.fragment:
        raise ValueError("host must not contain a path, query, or fragment")

    normalized = URL.build(scheme="http", host=url.host, port=port)
    return str(normalized).rstrip("/")


class GatewayApiClient:
    """Read the versioned OSK Sense external REST API."""

    def __init__(
        self,
        host: str,
        token: str,
        session: ClientSession,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if not token or any(not 0x21 <= ord(char) <= 0x7E for char in token):
            raise ValueError("token must be non-empty and contain no whitespace")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._base_url = normalize_base_url(host)
        self._token = token
        self._session = session
        self._timeout = ClientTimeout(total=timeout)
        self._timeout_seconds = timeout

    @property
    def base_url(self) -> str:
        """Return the normalized gateway base URL."""
        return self._base_url

    async def async_get_info(self) -> GatewayInfo:
        """Read unauthenticated gateway identity and compatibility data."""
        document = await self._async_get_json("/api/info", authenticated=False)
        return _parse_gateway_info(document)

    async def async_get_nodes(self) -> NodeRegistry:
        """Read the authenticated durable node registry."""
        document = await self._async_get_json("/api/nodes", authenticated=True)
        return _parse_node_registry(document)

    async def async_bootstrap(self) -> GatewayBootstrap:
        """Validate compatibility and read the state needed by the stream."""
        info = await self.async_get_info()
        if (
            info.api_version not in SUPPORTED_API_VERSIONS
            or info.stream_version not in SUPPORTED_STREAM_VERSIONS
        ):
            raise UnsupportedVersionError(info.api_version, info.stream_version)
        return GatewayBootstrap(info, await self.async_get_nodes())

    async def async_open_stream(
        self,
        bootstrap: GatewayBootstrap,
        *,
        manifest: ProtocolManifest | None = None,
    ) -> GatewayStream:
        """Open and validate one telemetry stream through its initial snapshot."""
        from .stream import GatewayStream

        websocket = await self._async_connect_websocket()
        stream = GatewayStream(self, websocket, bootstrap, manifest=manifest)
        try:
            await stream.async_read_snapshot()
        except BaseException:
            await websocket.close()
            raise
        return stream

    async def _async_connect_websocket(self) -> ClientWebSocketResponse:
        """Open the authenticated gateway WebSocket transport."""
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await self._session.ws_connect(
                    f"{self._base_url}/ws",
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "X-Client": CLIENT_NAME,
                    },
                    autoclose=True,
                    autoping=True,
                    max_msg_size=MAX_STREAM_MESSAGE_SIZE,
                )
        except WSServerHandshakeError as error:
            if error.status in (401, 403):
                raise AuthenticationError("invalid bearer token") from error
            raise CannotConnectError("cannot open telemetry stream") from error
        except (TimeoutError, ClientError, OSError) as error:
            raise CannotConnectError("cannot open telemetry stream") from error

    async def _async_get_json(self, path: str, *, authenticated: bool) -> JsonObject:
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            async with self._session.get(
                f"{self._base_url}{path}",
                headers=headers,
                timeout=self._timeout,
            ) as response:
                body = await response.content.read(MAX_RESPONSE_SIZE + 1)
                if authenticated and response.status in (401, 403):
                    raise AuthenticationError("invalid bearer token")
                if response.status != 200:
                    raise ApiResponseError(
                        response.status, _read_error_code(body, response.content_type)
                    )
                if len(body) > MAX_RESPONSE_SIZE:
                    raise InvalidResponseError("response exceeds size limit")
                if response.content_type != "application/json":
                    raise InvalidResponseError("response is not JSON")
        except AuthenticationError, ApiResponseError, InvalidResponseError:
            raise
        except (TimeoutError, ClientError, OSError) as error:
            raise CannotConnectError("cannot connect to gateway") from error

        try:
            value: object = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InvalidResponseError("response contains invalid JSON") from error
        if not isinstance(value, dict):
            raise InvalidResponseError("response root must be an object")
        return cast(JsonObject, value)


def _read_error_code(body: bytes, content_type: str) -> str | None:
    if content_type != "application/json" or len(body) > MAX_RESPONSE_SIZE:
        return None
    try:
        value: object = json.loads(body)
    except UnicodeDecodeError, json.JSONDecodeError:
        return None
    if isinstance(value, dict):
        error = cast(dict[object, object], value).get("error")
        if isinstance(error, str):
            return error
    return None


def _parse_gateway_info(document: JsonObject) -> GatewayInfo:
    ui_document = _object(document, "ui")
    firmware = _semver(document, "firmware_version")
    api_version = _uint(document, "api_version", 0xFFFFFFFF)
    stream_version = _uint(document, "stream_version", 0xFF)
    gateway_id = _matching_string(document, "gateway_id", _LOWER_HEX_128)
    boot_id = _matching_string(document, "boot_id", _LOWER_HEX_128)
    ui_version = _string(ui_document, "version")
    if ui_version and _SEMVER.fullmatch(ui_version) is None:
        raise InvalidResponseError("ui.version is not SemVer")
    return GatewayInfo(
        firmware_version=firmware,
        api_version=api_version,
        stream_version=stream_version,
        gateway_id=gateway_id,
        boot_id=boot_id,
        ui=GatewayUiInfo(
            state=_string(ui_document, "state"),
            version=ui_version,
            required_firmware=_string(ui_document, "required_firmware"),
        ),
        board=_string(document, "board"),
        hostname=_string(document, "hostname"),
    )


def _parse_node_registry(document: JsonObject) -> NodeRegistry:
    generation = _uint(document, "registry_generation", 0xFFFFFFFF)
    node_documents_value = document.get("nodes")
    if not isinstance(node_documents_value, list):
        raise InvalidResponseError("nodes must be an array")
    nodes: list[NodeInfo] = []
    node_ids: set[int] = set()
    device_uids: set[str] = set()
    for item in cast(list[object], node_documents_value):
        if not isinstance(item, dict):
            raise InvalidResponseError("each node must be an object")
        node = _parse_node(cast(JsonObject, item))
        if node.node_id in node_ids or node.device_uid in device_uids:
            raise InvalidResponseError("node IDs and device UIDs must be unique")
        node_ids.add(node.node_id)
        device_uids.add(node.device_uid)
        nodes.append(node)
    return NodeRegistry(generation, tuple(nodes))


def _parse_node(document: JsonObject) -> NodeInfo:
    has_telemetry = document.get("has_telemetry")
    if not isinstance(has_telemetry, bool):
        raise InvalidResponseError("has_telemetry must be a boolean")
    last_seen_value: object = document.get("last_seen_at_ms")
    rssi_value: object = document.get("rssi")
    if has_telemetry:
        if not _is_uint(last_seen_value, 0xFFFFFFFFFFFFFFFF):
            raise InvalidResponseError("last_seen_at_ms must be an unsigned integer")
        if not _is_int(rssi_value) or not -0x8000 <= rssi_value <= 0x7FFF:
            raise InvalidResponseError("rssi must be a signed 16-bit integer")
        last_seen: int | None = last_seen_value
        rssi: int | None = rssi_value
    elif "last_seen_at_ms" in document or "rssi" in document:
        raise InvalidResponseError(
            "telemetry metadata must be absent when has_telemetry is false"
        )
    else:
        last_seen = None
        rssi = None

    return NodeInfo(
        node_id=_uint(document, "node_id", 99, minimum=1),
        device_uid=_matching_string(document, "device_uid", _UPPER_HEX_UID),
        display_name=_string(document, "display_name"),
        profile_id=_uint(document, "profile_id", 0xFFFF, minimum=1),
        firmware=_semver(document, "firmware"),
        state=_string(document, "state"),
        has_telemetry=has_telemetry,
        last_seen_at_ms=last_seen,
        rssi=rssi,
    )


def _object(document: JsonObject, key: str) -> JsonObject:
    value = document.get(key)
    if not isinstance(value, dict):
        raise InvalidResponseError(f"{key} must be an object")
    return cast(JsonObject, value)


def _string(document: JsonObject, key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str):
        raise InvalidResponseError(f"{key} must be a string")
    return value


def _matching_string(document: JsonObject, key: str, pattern: re.Pattern[str]) -> str:
    value = _string(document, key)
    if pattern.fullmatch(value) is None:
        raise InvalidResponseError(f"{key} has an invalid format")
    return value


def _semver(document: JsonObject, key: str) -> str:
    value = _string(document, key)
    if _SEMVER.fullmatch(value) is None:
        raise InvalidResponseError(f"{key} is not SemVer")
    return value


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_uint(value: object, maximum: int, minimum: int = 0) -> TypeGuard[int]:
    return _is_int(value) and minimum <= value <= maximum


def _uint(document: JsonObject, key: str, maximum: int, *, minimum: int = 0) -> int:
    value = document.get(key)
    if not _is_uint(value, maximum, minimum):
        raise InvalidResponseError(f"{key} must be in {minimum}..{maximum}")
    return value
