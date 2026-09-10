"""Install requirements needed by the minimal interactive HA environment."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import homeassistant


def main() -> None:
    """Install the requirements declared by interactive system integrations."""
    components = Path(homeassistant.__file__).parent / "components"
    requirements: list[str] = []

    for domain in ("frontend",):
        manifest_path = components / domain / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        requirements.extend(manifest.get("requirements", []))

    if requirements:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", *requirements], check=True
        )


if __name__ == "__main__":
    main()
