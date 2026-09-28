# Contributing to OSK Sense for Home Assistant

## Development environment

The recommended environment is the repository dev container. In VS Code, run **Dev Containers: Rebuild and Reopen in Container**. It provides the Python and Home Assistant dependencies used by the test suite.

Run all tests, including the Home Assistant config-flow tests, with:

```bash
python -m pytest -v
```

Start a development Home Assistant instance with:

```bash
bash scripts/develop
```

Home Assistant will be available at <http://localhost:8123>. Its local runtime configuration is stored in the ignored `config` directory.

## Protocol updates

The canonical wire-format files live in the sibling [`osk-sense`](https://github.com/strange-v/osk-sense) repository. Update the vendored artifacts after every protocol change:

```bash
python scripts/sync_protocol_artifacts.py
```

The REST and WebSocket clients are independent of Home Assistant. The WebSocket client validates `HELLO`, reconciles registry generations, accepts only a complete snapshot, and decodes attributed node telemetry through the vendored protocol manifest. The stream uses a 30-second heartbeat and reconnects with bounded exponential backoff.
