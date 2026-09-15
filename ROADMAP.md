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
- State-aware node lifecycle: ignore never-active pending/disabled nodes, retain
  temporarily inactive devices as unavailable, and remove devices deleted from the
  gateway registry.
- Five-byte telemetry prefix with transmit power, radio fallback, and downlink
  signal diagnostics.
- Token reauthentication with gateway identity verification and automatic reload.
- Manifest-driven sensor and binary-sensor platforms with stable unique IDs.
- Entity availability for disconnected gateways and telemetry older than 2h15m.
- HA lifecycle coverage for deferred startup, push updates, and clean unload.
- Recorder/history support and sensible display precision for numeric sensors.
- Per-node pulse-counter options for units per pulse, unit, and device class.
- Raw pulse count alongside an optional converted total, including metric,
  non-metric, and custom units.
- Local integration icon and logo assets.
- Downloadable diagnostics with redacted credentials, network details, and
  hardware identifiers.
- Gateway health entities for connection, active nodes, last restart, last stream
  message, and reconnect count.
- Real-hardware validation with one gateway and one temperature node, including
  restart, uptime, Ethernet recovery, reconnect metrics, stream timestamps, and
  node renaming.
- Real-hardware validation of binary-state and pulse-counter profiles.
- Home Assistant dev container and automated tests.

## Configuration and discovery

- Add mDNS discovery and update a gateway's address by its stable gateway ID
  without creating a duplicate config entry.

## Release readiness

- Exercise registry changes and long-offline behavior on real hardware.
- Exercise a gateway with multiple nodes on real hardware.
- Complete user documentation, HACS metadata, CI, and release packaging.
