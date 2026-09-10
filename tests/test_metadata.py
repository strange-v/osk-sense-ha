"""Validate integration metadata and translations without Home Assistant."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

INTEGRATION = Path(__file__).parents[1] / "custom_components" / "osk_sense"


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def _shape(value: object) -> object:
    if isinstance(value, dict):
        return {key: _shape(item) for key, item in value.items()}
    return type(value)


class IntegrationMetadataTest(unittest.TestCase):
    """Check metadata that HA normally validates with hassfest."""

    def test_manifest_declares_hub_config_flow(self) -> None:
        manifest = _load_json(INTEGRATION / "manifest.json")
        self.assertEqual("osk_sense", manifest["domain"])
        self.assertEqual("hub", manifest["integration_type"])
        self.assertEqual("local_push", manifest["iot_class"])
        self.assertIs(manifest["config_flow"], True)

    def test_translations_have_the_same_shape_as_strings(self) -> None:
        strings = _load_json(INTEGRATION / "strings.json")["config"]
        for language in ("en", "uk"):
            with self.subTest(language):
                translation = _load_json(
                    INTEGRATION / "translations" / f"{language}.json"
                )["config"]
                self.assertEqual(_shape(strings), _shape(translation))


if __name__ == "__main__":
    unittest.main()
