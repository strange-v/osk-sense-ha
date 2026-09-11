"""Shared entities backed by the OSK Sense gateway runtime."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_point_in_utc_time

from .const import DOMAIN, STALE_AFTER_SECONDS

if TYPE_CHECKING:
    from .api import NodeInfo
    from .runtime import GatewayRuntime
    from .stream import TelemetryEvent


class OskSenseEntity(Entity):
    """Base for one field belonging to a durable OSK Sense node."""

    _attr_has_entity_name = True

    def __init__(self, runtime: GatewayRuntime, node: NodeInfo, key: str) -> None:
        self._runtime = runtime
        self._device_uid = node.device_uid
        self._key = key
        self._attr_unique_id = f"{node.device_uid}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, node.device_uid)},
        )
        self._cancel_stale_timer: Callable[[], None] | None = None
        self._attr_available = self._calculate_available()

    def _calculate_available(self) -> bool:
        """Calculate connection, registry, and telemetry availability."""
        node = self._node
        if not self._runtime.connected or node is None or node.state != "active":
            return False
        event = self._event
        if event is None:
            return True
        age = time.time() - event.received_at_unix_ms / 1000
        return age <= STALE_AFTER_SECONDS

    @property
    def _node(self) -> NodeInfo | None:
        return next(
            (
                node
                for node in self._runtime.registry.nodes
                if node.device_uid == self._device_uid
            ),
            None,
        )

    @property
    def _event(self) -> TelemetryEvent | None:
        return self._runtime.latest.get(self._device_uid)

    async def async_added_to_hass(self) -> None:
        """Subscribe to push updates."""
        self.async_on_remove(self._runtime.async_add_listener(self._handle_update))
        self.async_on_remove(self._async_cancel_stale_timer)
        self._async_schedule_stale_timer()

    @callback
    def _handle_update(self) -> None:
        self._attr_available = self._calculate_available()
        self._update_value()
        self._async_schedule_stale_timer()
        self.async_write_ha_state()

    @callback
    def _update_value(self) -> None:
        """Copy current runtime data into platform-specific state attributes."""

    @callback
    def _async_schedule_stale_timer(self) -> None:
        self._async_cancel_stale_timer()
        if (event := self._event) is None:
            return
        stale_at = event.received_at_unix_ms / 1000 + STALE_AFTER_SECONDS
        if stale_at <= time.time():
            return
        assert self.hass is not None
        self._cancel_stale_timer = async_track_point_in_utc_time(
            self.hass,
            self._async_mark_stale,
            datetime.fromtimestamp(stale_at, UTC),
        )

    @callback
    def _async_mark_stale(self, _: datetime) -> None:
        self._cancel_stale_timer = None
        self._attr_available = self._calculate_available()
        self.async_write_ha_state()

    @callback
    def _async_cancel_stale_timer(self) -> None:
        if self._cancel_stale_timer is not None:
            self._cancel_stale_timer()
            self._cancel_stale_timer = None
