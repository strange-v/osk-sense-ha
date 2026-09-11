"""Long-lived runtime state for one OSK Sense gateway."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any, Final

from .api import GatewayApiClient, GatewayBootstrap
from .protocol import ProtocolManifest
from .stream import GatewayStream, RegistryUpdatedEvent, TelemetryEvent

_LOGGER = logging.getLogger(__name__)

_MAX_SEQUENCE: Final = 0xFFFFFFFF
_SEQUENCE_HALF_RANGE: Final = 0x80000000
_MAX_RECONNECT_DELAY: Final = 60.0
_STREAM_FAIRNESS_DELAY: Final = 0.001

RuntimeListener = Callable[[], None]
Sleep = Callable[[float], Coroutine[Any, Any, None]]


def sequence_is_newer(candidate: int, previous: int) -> bool:
    """Compare wrapping unsigned 32-bit stream sequence numbers."""
    difference = (candidate - previous) & _MAX_SEQUENCE
    return 0 < difference < _SEQUENCE_HALF_RANGE


class GatewayRuntime:
    """Maintain registry and latest telemetry across stream reconnects."""

    def __init__(
        self,
        client: GatewayApiClient,
        bootstrap: GatewayBootstrap,
        *,
        manifest: ProtocolManifest | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.client = client
        self.bootstrap = bootstrap
        self.manifest = manifest or ProtocolManifest.load_default()
        self.registry = bootstrap.registry
        self.latest: dict[str, TelemetryEvent] = {}
        self.connected = False
        self._sleep = sleep
        self._listeners: set[RuntimeListener] = set()
        self._stopping = False
        self._stream: GatewayStream | None = None

    def async_add_listener(self, listener: RuntimeListener) -> Callable[[], None]:
        """Subscribe to runtime state changes."""
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    async def async_run(self) -> None:
        """Connect forever, backing off after any failed stream session."""
        delay = 1.0
        current_bootstrap = self.bootstrap
        while not self._stopping:
            try:
                stream = await self.client.async_open_stream(
                    current_bootstrap, manifest=self.manifest
                )
                self._stream = stream
                self.bootstrap = current_bootstrap
                self._apply_snapshot(stream)
                self.connected = True
                self._notify()
                delay = 1.0
                while not self._stopping:
                    self._apply_event(await stream.async_receive())
                    # aiohttp may satisfy receive() synchronously while its queue is
                    # buffered. Always yield so a busy gateway cannot starve HA's
                    # event loop and prevent the startup lifecycle from completing.
                    await asyncio.sleep(_STREAM_FAIRNESS_DELAY)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # The supervisor must survive bad sessions.
                _LOGGER.warning("OSK Sense stream session failed: %s", error)
            finally:
                if self._stream is not None:
                    await self._stream.async_close()
                    self._stream = None
                if self.connected:
                    self.connected = False
                    self._notify()

            if self._stopping:
                break
            await self._sleep(delay)
            delay = min(delay * 2, _MAX_RECONNECT_DELAY)
            try:
                current_bootstrap = await self.client.async_bootstrap()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                _LOGGER.warning("OSK Sense re-bootstrap failed: %s", error)

    async def async_stop(self) -> None:
        """Request shutdown and close a receive-blocked stream."""
        self._stopping = True
        if self._stream is not None:
            await self._stream.async_close()

    def _apply_snapshot(self, stream: GatewayStream) -> None:
        snapshot = stream.snapshot
        self.registry = snapshot.registry
        self.latest = {event.node.device_uid: event for event in snapshot.telemetry}

    def _apply_event(self, event: TelemetryEvent | RegistryUpdatedEvent) -> None:
        if isinstance(event, RegistryUpdatedEvent):
            self.registry = event.registry
            nodes_by_uid = {node.device_uid: node for node in event.registry.nodes}
            self.latest = {
                uid: telemetry
                for uid, telemetry in self.latest.items()
                if (node := nodes_by_uid.get(uid)) is not None
                and node.profile_id == telemetry.node.profile_id
            }
            self._notify()
            return

        previous = self.latest.get(event.node.device_uid)
        if previous is not None and not sequence_is_newer(
            event.sequence, previous.sequence
        ):
            return
        self.latest[event.node.device_uid] = event
        self._notify()

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()
