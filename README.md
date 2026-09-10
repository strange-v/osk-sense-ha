# osk-sense-ha
Local Home Assistant integration for OSK Sense Hub. Reads the node registry over REST and streams telemetry from paired OSK Sense nodes over WebSocket. Read-only, no cloud. Install through HACS.

## Development

The canonical wire-format files live in the sibling `RadioSensors` repository. Vendor them after every protocol change:

```powershell
python scripts/sync_protocol_artifacts.py
```

Run the dependency-free protocol tests with:

```powershell
python -m unittest discover -v
```
