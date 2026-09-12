"""Diagnostics support for OSK Sense."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, cast

from homeassistant.components.diagnostics import (
    async_redact_data,  # pyright: ignore[reportUnknownVariableType]
)
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import IntegrationConfigEntry
from .const import CONF_PULSE_COUNTERS, CONF_TOKEN

_CONFIG_REDACT = {CONF_HOST, CONF_TOKEN}
_GATEWAY_REDACT = {"boot_id", "gateway_id", "hostname"}
_NODE_REDACT = {"display_name"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: IntegrationConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for an OSK Sense config entry."""
    runtime = entry.runtime_data
    aliases = {
        node.device_uid: f"node_{index}"
        for index, node in enumerate(runtime.registry.nodes, start=1)
    }
    nodes: list[dict[str, Any]] = []
    for node in runtime.registry.nodes:
        node_data = asdict(node)
        node_data.pop("device_uid")
        event = runtime.latest.get(node.device_uid)
        nodes.append(
            {
                "identifier": aliases[node.device_uid],
                "registry": async_redact_data(node_data, _NODE_REDACT),
                "telemetry": (
                    {
                        "sequence": event.sequence,
                        "received_at_unix_ms": event.received_at_unix_ms,
                        "rssi": event.rssi,
                        "profile_id": event.telemetry.profile_id,
                        "profile_name": event.telemetry.profile_name,
                        "raw": _json_safe(event.telemetry.raw),
                        "values": _json_safe(event.telemetry.values),
                    }
                    if event is not None
                    else None
                ),
            }
        )

    gateway = asdict(runtime.bootstrap.info)
    gateway["uptime_seconds"] = runtime.gateway_uptime_seconds

    return {
        "config_entry": {
            "data": async_redact_data(dict(entry.data), _CONFIG_REDACT),
            "options": _redacted_options(dict(entry.options), aliases),
        },
        "gateway": async_redact_data(gateway, _GATEWAY_REDACT),
        "runtime": {
            "connected": runtime.connected,
            "registry_generation": runtime.registry.generation,
            "node_count": len(runtime.registry.nodes),
            "telemetry_count": len(runtime.latest),
            "last_stream_message_at_unix_ms": (runtime.last_stream_message_at_unix_ms),
            "reconnect_count": runtime.reconnect_count,
        },
        "nodes": nodes,
    }


def _redacted_options(
    options: dict[str, Any], aliases: dict[str, str]
) -> dict[str, Any]:
    """Replace hardware identifiers used as pulse-counter option keys."""
    result = dict(options)
    counters = result.get(CONF_PULSE_COUNTERS)
    if isinstance(counters, dict):
        typed_counters = cast(dict[str, Any], counters)
        result[CONF_PULSE_COUNTERS] = {
            aliases.get(device_uid, "unknown_node"): _json_safe(config)
            for device_uid, config in typed_counters.items()
        }
    return _json_safe(result)


def _json_safe(value: Any) -> Any:
    """Convert protocol values to structures accepted by the JSON serializer."""
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        mapping = cast(dict[object, Any], value)
        return {str(key): _json_safe(item) for key, item in mapping.items()}
    if isinstance(value, list | tuple):
        items = cast(list[Any] | tuple[Any, ...], value)
        return [_json_safe(item) for item in items]
    return value
