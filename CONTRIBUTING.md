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

## Release

1. Update the version in `pyproject.toml`, `custom_components/osk_sense/manifest.json`, and the `CLIENT_NAME` value in `custom_components/osk_sense/api.py` to the same SemVer value.
2. In the Linux dev container, run `python -m pytest -v`, `ruff check custom_components/osk_sense tests`, `ruff format --check custom_components/osk_sense tests`, and `pyright custom_components/osk_sense`.
3. Push the change and check that the HACS and Hassfest metadata validation jobs pass on GitHub.
4. Create a GitHub Release from that commit with tag `X.Y.Z` and release notes describing user-visible changes. HACS downloads the integration from the repository; no wheel or ZIP asset is needed.
