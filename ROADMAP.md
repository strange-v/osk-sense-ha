# Roadmap

## Completed

- Protocol manifest and test vectors vendored from the firmware repository.
- Dependency-free telemetry and gateway-stream decoder.
- Async REST client, config flow, and gateway/node device registration.
- Validated WebSocket client with HELLO, snapshot, registry reconciliation, and
  telemetry decoding.
- Home Assistant dev container and automated tests.

## Next: Home Assistant runtime

- Add one background stream task per config entry.
- Reconnect with bounded exponential backoff after disconnects or invalid state.
- Maintain the current registry and latest telemetry per durable node identity.
- Apply wrapping sequence comparison and ignore duplicate or stale telemetry.
- Distinguish entity state correctly:
  - no telemetry in a completed snapshot: `unknown`;
  - disconnected gateway: `unavailable`;
  - no update for 2 hours 15 minutes: `unavailable`.
- Refresh HA devices after `REGISTRY_CHANGED`, including gateway/node renames.
- Add focused runtime tests for reconnects, registry races, stale data, and unload.

## Entity platforms

- Create `sensor` entities from manifest fields for supported profiles.
- Create `binary_sensor` entities for binary-state fields.
- Publish voltage, temperature, humidity, pressure, RSSI, timestamps, and raw pulse
  count with stable unique IDs based on `device_uid` and `field.name`.
- Map units, device classes, state classes, diagnostics, and availability to Home
  Assistant conventions.

## Configuration and discovery

- Add options for node type overrides where the profile is insufficient.
- Add pulse-counter options: units per pulse, unit, and device class.
- Publish both the always-present raw counter and the configured converted total.
- Handle gateway hostname changes without creating a duplicate config entry.
- Add mDNS discovery after manual setup is stable.

## Release readiness

- Exercise setup, reconnect, rename, registry change, and long-offline behavior on
  real hardware.
- Add diagnostics with gateway information and redacted configuration data.
- Complete user documentation, HACS metadata, CI, and release packaging.
