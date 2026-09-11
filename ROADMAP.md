# Roadmap

## Completed

- Protocol manifest and test vectors vendored from the firmware repository.
- Dependency-free telemetry and gateway-stream decoder.
- Async REST client, config flow, and gateway/node device registration.
- Validated WebSocket client with HELLO, snapshot, registry reconciliation, and
  telemetry decoding.
- Per-entry stream supervisor with bounded reconnect backoff and clean shutdown.
- Runtime registry/latest-telemetry state with wrapping sequence deduplication.
- Device registry refresh after gateway or node metadata changes.
- Manifest-driven sensor and binary-sensor platforms with stable unique IDs.
- Entity availability for disconnected gateways and telemetry older than 2h15m.
- Home Assistant dev container and automated tests.

## Next: Runtime hardening

- Add focused runtime tests for reconnects, registry races, stale data, and unload.

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
