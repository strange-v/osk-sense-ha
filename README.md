# OSK Sense for Home Assistant

Local, read-only Home Assistant integration for [OSK Sense](https://github.com/strange-v/osk-sense) gateways and their paired sensor nodes. It reads the gateway's node registry over REST and streams telemetry over WebSocket. No cloud account is required.

Requires Home Assistant **2026.3.0 or newer** and an OSK Sense gateway.

## Install

1. In HACS, open **Custom repositories**, add `https://github.com/strange-v/osk-sense-ha`, and choose **Integration**.
2. Find **OSK Sense** in HACS and download it. Restart Home Assistant.
3. In the gateway web UI, open **Connect Home Assistant** and create a connection key. Copy it when shown; the key needs `telemetry:read` access.
4. In Home Assistant, open **Settings → Devices & services → Add integration**, select **OSK Sense**, and enter the gateway host (for example, `osk-hub-<MAC>.local` or its IP address) and the connection key.

The gateway and Home Assistant must be able to reach each other on the local network. The gateway API uses HTTP, so keep that network trusted. If HACS is unavailable, copy `custom_components/osk_sense` into your Home Assistant `config/custom_components/` directory, restart, and continue at step 3.

## What appears in Home Assistant

The gateway has connection state, active-node count, and last-restart entities. Active nodes have entities for their supported measurements, such as supply voltage, temperature, humidity, pressure, raw pulse count, and binary state. Radio and stream diagnostics are disabled by default; enable them from the entity settings if needed.

Node entities become unavailable when the gateway disconnects or a node has not reported for 2 hours 15 minutes. Pending or disabled nodes that were previously active remain registered but unavailable. Deleting a node from the gateway removes its Home Assistant device and entities.

For a pulse-counter node, open the integration's **Configure** menu to set units per pulse, unit, and device class. The raw pulse count remains available alongside the optional converted total.

If a connection key expires or is revoked, Home Assistant offers a reauthentication repair. Create a new key on the same gateway and enter it in the repair flow; device and entity identities are preserved.

For development setup, tests, and protocol updates, see [CONTRIBUTING.md](CONTRIBUTING.md).
