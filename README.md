# osk-sense-ha
Local Home Assistant integration for OSK Sense Hub. Reads the node registry over REST and streams telemetry from paired OSK Sense nodes over WebSocket. Read-only, no cloud. Install through HACS.

## Development

The canonical wire-format files live in the sibling `RadioSensors` repository. Vendor them after every protocol change:

```powershell
python scripts/sync_protocol_artifacts.py
```

Run the dependency-free protocol tests with:

```powershell
python -m unittest tests.test_protocol -v
```

The REST client is async and independent of Home Assistant. Callers inject their
own `aiohttp.ClientSession`; the client never owns or closes it.

The gateway WebSocket client is independent of Home Assistant as well. It
validates `HELLO`, reconciles registry generations, accepts only a complete
snapshot, and decodes attributed node telemetry through the vendored protocol
manifest before exposing live events.

Home Assistant creates manifest-driven sensors for supply voltage, temperature,
humidity, pressure, and raw pulse count, plus binary sensors for binary-state
profiles. RSSI and last-telemetry timestamp are available as disabled-by-default
diagnostic entities. Entities become unavailable while the gateway is disconnected
or after 2 hours 15 minutes without telemetry.

The custom integration targets Home Assistant 2026.8 or newer. Add OSK Sense
from **Settings → Devices & services**, then enter the gateway host and a bearer
token with the `telemetry:read` scope.

The recommended development environment is the repository dev container. In
VS Code, run **Dev Containers: Rebuild and Reopen in Container**. Then run the
complete suite, including the Home Assistant config-flow tests, with:

```bash
python -m pytest -v
```

Start a development Home Assistant instance with:

```bash
bash scripts/develop
```

Home Assistant will be available at <http://localhost:8123>. Its local runtime
configuration is stored in the ignored `config` directory.
