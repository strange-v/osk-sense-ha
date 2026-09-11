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
- HA lifecycle coverage for deferred startup, push updates, and clean unload.
- Recorder/history support and sensible display precision for numeric sensors.
- Per-node pulse-counter options for units per pulse, unit, and device class.
- Raw pulse count alongside an optional converted total, including metric,
  non-metric, and custom units.
- Local integration icon and logo assets.
- Home Assistant dev container and automated tests.

## Configuration and discovery

- Evaluate node type overrides if a future profile cannot describe a device
  unambiguously.
- Add mDNS discovery and update a gateway's address by its stable gateway ID
  without creating a duplicate config entry.

## Release readiness

- Exercise setup, reconnect, rename, registry change, and long-offline behavior on
  real hardware.
- Add diagnostics with gateway information and redacted configuration data.
- Complete user documentation, HACS metadata, CI, and release packaging.
