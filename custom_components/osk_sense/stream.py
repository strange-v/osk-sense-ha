"""Validated async telemetry stream for the OSK Sense Hub."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

from aiohttp import ClientWebSocketResponse, WSMsgType

from .protocol import (
    DecodedStreamMessage,
    DecodedTelemetry,
    DecodeError,
    ProtocolManifest,
    UnknownProfileError,
    UnknownStreamMessage,
)

if TYPE_CHECKING:
    from .api import GatewayApiClient, GatewayBootstrap, NodeInfo, NodeRegistry

_HELLO: Final = "hello"
_SNAPSHOT_BEGIN: Final = "snapshot_begin"
_TELEMETRY: Final = "telemetry"
_SNAPSHOT_END: Final = "snapshot_end"
_REGISTRY_CHANGED: Final = "registry_changed"


class StreamError(Exception):
    """Base exception for a telemetry stream that cannot be trusted."""


class StreamDisconnectedError(StreamError):
    """The WebSocket closed or failed."""


class InvalidStreamError(StreamError):
    """The WebSocket violated the documented stream lifecycle."""


class StreamResyncRequiredError(StreamError):
    """Registry state changed before a safe mapping could be established."""


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    """One telemetry record attributed to a durable node identity."""

    sequence: int
    node: NodeInfo
    received_at_unix_ms: int
    rssi: int
    telemetry: DecodedTelemetry


@dataclass(frozen=True, slots=True)
class RegistryUpdatedEvent:
    """A registry replacement announced by the live stream."""

    registry: NodeRegistry


@dataclass(frozen=True, slots=True)
class StreamSnapshot:
    """A complete, generation-consistent gateway telemetry snapshot."""

    registry: NodeRegistry
    telemetry: tuple[TelemetryEvent, ...]


StreamEvent = TelemetryEvent | RegistryUpdatedEvent


class GatewayStream:
    """Consume one connected and identity-checked gateway stream."""

    def __init__(
        self,
        api: GatewayApiClient,
        websocket: ClientWebSocketResponse,
        bootstrap: GatewayBootstrap,
        *,
        manifest: ProtocolManifest | None = None,
    ) -> None:
        self._api = api
        self._websocket = websocket
        self._bootstrap = bootstrap
        self._manifest = manifest or ProtocolManifest.load_default()
        self._registry = bootstrap.registry
        self._snapshot: StreamSnapshot | None = None

    @property
    def snapshot(self) -> StreamSnapshot:
        """Return the initial snapshot after it has been read."""
        if self._snapshot is None:
            raise RuntimeError("stream snapshot has not been read")
        return self._snapshot

    @property
    def closed(self) -> bool:
        """Return whether the underlying WebSocket is closed."""
        return self._websocket.closed

    async def async_read_snapshot(self) -> StreamSnapshot:
        """Validate HELLO and consume one complete initial snapshot."""
        if self._snapshot is not None:
            raise RuntimeError("stream snapshot has already been read")

        first_message = await self._async_receive()
        if (
            not isinstance(first_message, DecodedStreamMessage)
            or first_message.name != _HELLO
        ):
            raise InvalidStreamError("HELLO must be the first stream message")
        hello = first_message
        self._validate_hello(hello)

        hello_generation = _integer_field(hello, "registry_generation")
        if hello_generation != self._registry.generation:
            self._registry = await self._api.async_get_nodes()
            if self._registry.generation != hello_generation:
                raise StreamResyncRequiredError(
                    "registry changed while reconciling HELLO"
                )

        begin = await self._async_receive_known()
        if begin.name != _SNAPSHOT_BEGIN:
            raise InvalidStreamError("SNAPSHOT_BEGIN must follow HELLO")
        begin_generation = _integer_field(begin, "registry_generation")
        if begin_generation != self._registry.generation:
            raise StreamResyncRequiredError("snapshot registry generation is stale")

        telemetry: list[TelemetryEvent] = []
        seen_nodes: set[int] = set()
        while True:
            message = await self._async_receive_known()
            if message.name == _SNAPSHOT_END:
                end_generation = _integer_field(message, "registry_generation")
                if end_generation != begin_generation:
                    raise StreamResyncRequiredError(
                        "registry changed during the telemetry snapshot"
                    )
                break
            if message.name != _TELEMETRY:
                raise InvalidStreamError(
                    f"unexpected {message.name} inside telemetry snapshot"
                )
            event = self._decode_telemetry(message)
            if event.node.node_id in seen_nodes:
                raise InvalidStreamError("snapshot contains duplicate node telemetry")
            seen_nodes.add(event.node.node_id)
            telemetry.append(event)

        self._snapshot = StreamSnapshot(self._registry, tuple(telemetry))
        return self._snapshot

    async def async_receive(self) -> StreamEvent:
        """Receive the next actionable live event, ignoring future message kinds."""
        if self._snapshot is None:
            raise RuntimeError("stream snapshot has not been read")
        while True:
            message = await self._async_receive()
            if isinstance(message, UnknownStreamMessage):
                continue
            if message.name == _TELEMETRY:
                return self._decode_telemetry(message)
            if message.name == _REGISTRY_CHANGED:
                generation = _integer_field(message, "registry_generation")
                registry = await self._api.async_get_nodes()
                if registry.generation != generation:
                    raise StreamResyncRequiredError(
                        "registry changed again while refreshing nodes"
                    )
                self._registry = registry
                return RegistryUpdatedEvent(registry)
            raise InvalidStreamError(f"unexpected live stream message {message.name}")

    async def async_close(self) -> None:
        """Close the underlying WebSocket."""
        await self._websocket.close()

    async def __aenter__(self) -> GatewayStream:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.async_close()

    async def _async_receive_known(self) -> DecodedStreamMessage:
        while True:
            message = await self._async_receive()
            if isinstance(message, DecodedStreamMessage):
                return message

    async def _async_receive(
        self,
    ) -> DecodedStreamMessage | UnknownStreamMessage:
        message = await self._websocket.receive()
        if message.type is WSMsgType.BINARY:
            try:
                return self._manifest.decode_stream(cast(bytes, message.data))
            except DecodeError as error:
                raise InvalidStreamError(error.code) from error
        if message.type is WSMsgType.ERROR:
            raise StreamDisconnectedError(
                "telemetry WebSocket failed"
            ) from self._websocket.exception()
        if message.type in {
            WSMsgType.CLOSE,
            WSMsgType.CLOSED,
            WSMsgType.CLOSING,
        }:
            raise StreamDisconnectedError("telemetry WebSocket closed")
        raise InvalidStreamError("gateway sent a non-binary WebSocket message")

    def _validate_hello(self, message: DecodedStreamMessage) -> None:
        info = self._bootstrap.info
        if _string_field(message, "gateway_id") != info.gateway_id:
            raise InvalidStreamError("HELLO gateway_id does not match REST bootstrap")
        if _string_field(message, "boot_id") != info.boot_id:
            raise InvalidStreamError("HELLO boot_id does not match REST bootstrap")

    def _decode_telemetry(self, message: DecodedStreamMessage) -> TelemetryEvent:
        node_id = _integer_field(message, "node_id")
        profile_id = _integer_field(message, "profile_id")
        node = next(
            (item for item in self._registry.nodes if item.node_id == node_id), None
        )
        if node is None or node.state != "active":
            raise InvalidStreamError("telemetry references an unknown or inactive node")
        if profile_id != node.profile_id:
            raise InvalidStreamError("telemetry profile does not match node registry")
        payload = _bytes_field(message, "payload")
        try:
            telemetry = self._manifest.decode_telemetry(profile_id, payload)
        except (DecodeError, UnknownProfileError) as error:
            raise InvalidStreamError(str(error)) from error
        return TelemetryEvent(
            sequence=_integer_field(message, "sequence"),
            node=node,
            received_at_unix_ms=_integer_field(message, "received_at_unix_ms"),
            rssi=_integer_field(message, "rssi"),
            telemetry=telemetry,
        )


def _integer_field(message: DecodedStreamMessage, name: str) -> int:
    value = message.fields.get(name)
    if not isinstance(value, int):
        raise InvalidStreamError(f"stream field {name} is not an integer")
    return value


def _string_field(message: DecodedStreamMessage, name: str) -> str:
    value = message.fields.get(name)
    if not isinstance(value, str):
        raise InvalidStreamError(f"stream field {name} is not a string")
    return value


def _bytes_field(message: DecodedStreamMessage, name: str) -> bytes:
    value = message.fields.get(name)
    if not isinstance(value, bytes):
        raise InvalidStreamError(f"stream field {name} is not bytes")
    return value
