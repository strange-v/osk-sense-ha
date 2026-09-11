"""Validate integration metadata and translations without Home Assistant."""

from __future__ import annotations

import json
import struct
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

    def test_local_brand_images_are_valid_pngs(self) -> None:
        """Ensure the packaged icon and logo have usable PNG canvases."""
        expected_sizes = {"icon.png": (512, 512), "logo.png": (512, 512)}
        for filename, expected_size in expected_sizes.items():
            with self.subTest(filename):
                data = (INTEGRATION / "brand" / filename).read_bytes()
                self.assertEqual(b"\x89PNG\r\n\x1a\n", data[:8])
                self.assertEqual(expected_size, struct.unpack(">II", data[16:24]))


if __name__ == "__main__":
    unittest.main()
