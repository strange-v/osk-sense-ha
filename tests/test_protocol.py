"""Known-answer and defensive tests for the protocol decoder."""

from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path

from custom_components.osk_sense.protocol import (
    DecodedStreamMessage,
    DecodeError,
    ManifestError,
    ProtocolManifest,
    UnknownProfileError,
    UnknownStreamMessage,
)

DATA = Path(__file__).parents[1] / "custom_components" / "osk_sense" / "protocol_data"


def load_json(name: str) -> dict:
    """Load a vendored protocol artifact."""
    with (DATA / name).open(encoding="utf-8") as file:
        return json.load(file)


class ProtocolDecoderTest(unittest.TestCase):
    """Verify the Python decoder against the shared vectors."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest_document = load_json("protocol-manifest.json")
        cls.vectors = load_json("protocol-vectors.json")
        cls.protocol = ProtocolManifest(cls.manifest_document)

    def test_default_manifest_loads(self) -> None:
        self.assertEqual(8, len(ProtocolManifest.load_default().profiles))

    def test_telemetry_vectors(self) -> None:
        for vector in self.vectors["telemetry"]:
            with self.subTest(vector["name"]):
                decoded = self.protocol.decode_telemetry(
                    vector["profile_id"], bytes.fromhex(vector["hex"])
                )
                self.assertEqual(vector["raw"], decoded.raw)
                self.assertEqual(vector["values"], decoded.values)

    def test_invalid_telemetry_vectors(self) -> None:
        for vector in self.vectors["invalid_telemetry"]:
            with self.subTest(vector["name"]):
                with self.assertRaisesRegex(DecodeError, f"^{vector['error']}$"):
                    self.protocol.decode_telemetry(
                        vector["profile_id"], bytes.fromhex(vector["hex"])
                    )

    def test_unknown_profile_is_distinct(self) -> None:
        with self.assertRaises(UnknownProfileError) as context:
            self.protocol.decode_telemetry(999, b"\x40")
        self.assertEqual(999, context.exception.profile_id)

    def test_gateway_stream_vectors(self) -> None:
        for vector in self.vectors["gateway_stream"]:
            with self.subTest(vector["name"]):
                decoded = self.protocol.decode_stream(bytes.fromhex(vector["hex"]))
                self.assertIsInstance(decoded, DecodedStreamMessage)
                assert isinstance(decoded, DecodedStreamMessage)
                self.assertEqual(vector["message"], decoded.name)
                actual = dict(decoded.fields)
                for key, expected in vector["decoded"].items():
                    value = actual[key]
                    if isinstance(value, bytes):
                        value = value.hex().upper()
                    elif isinstance(expected, str) and isinstance(value, int):
                        value = str(value)
                    self.assertEqual(expected, value, key)

    def test_unknown_stream_kind_can_be_ignored(self) -> None:
        decoded = self.protocol.decode_stream(bytes.fromhex("016304030201"))
        self.assertEqual(
            UnknownStreamMessage(1, 99, 0x01020304, bytes.fromhex("016304030201")),
            decoded,
        )

    def test_stream_rejects_wrong_version_and_length(self) -> None:
        with self.assertRaisesRegex(DecodeError, "^unsupported_version$"):
            self.protocol.decode_stream(bytes.fromhex("020104030201"))
        with self.assertRaisesRegex(DecodeError, "^wrong_length$"):
            self.protocol.decode_stream(bytes.fromhex("0101"))
        with self.assertRaisesRegex(DecodeError, "^wrong_length$"):
            self.protocol.decode_stream(bytes.fromhex("010104030201"))

    def test_field_name_semantics_are_stable_across_profiles(self) -> None:
        document = deepcopy(self.manifest_document)
        document["telemetry"]["profiles"][2]["fields"][0]["unit"] = "K"
        with self.assertRaisesRegex(ManifestError, "changes quantity or unit"):
            ProtocolManifest(document)

    def test_field_names_are_unique_in_complete_profile(self) -> None:
        document = deepcopy(self.manifest_document)
        duplicate = deepcopy(document["telemetry"]["common_fields"][0])
        document["telemetry"]["profiles"][0]["fields"].append(duplicate)
        with self.assertRaisesRegex(ManifestError, "duplicate telemetry field"):
            ProtocolManifest(document)

    def test_bit_field_masks_cannot_overlap(self) -> None:
        document = deepcopy(self.manifest_document)
        radio_state = document["telemetry"]["common_fields"][1]
        radio_state["bits"][1]["mask"] = 1
        with self.assertRaisesRegex(ManifestError, "overlaps"):
            ProtocolManifest(document)


if __name__ == "__main__":
    unittest.main()
