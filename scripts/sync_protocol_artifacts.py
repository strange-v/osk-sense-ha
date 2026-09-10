"""Vendor canonical protocol artifacts from the RadioSensors repository."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ARTIFACTS = (
    "protocol-manifest.json",
    "protocol-manifest.schema.json",
    "protocol-vectors.json",
)


def main() -> None:
    """Copy protocol artifacts into the integration package."""
    repository = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=repository.parent / "RadioSensors" / "v2" / "protocol",
    )
    args = parser.parse_args()
    destination = repository / "custom_components" / "osk_sense" / "protocol_data"
    destination.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACTS:
        source = args.source / name
        if not source.is_file():
            parser.error(f"missing protocol artifact: {source}")
        shutil.copyfile(source, destination / name)
        print(f"updated {destination / name}")


if __name__ == "__main__":
    main()
