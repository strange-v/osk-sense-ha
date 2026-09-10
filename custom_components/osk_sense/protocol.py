"""Decode OSK Sense gateway and radio telemetry wire formats."""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

JsonObject = dict[str, Any]

_INTEGER_TYPES: Final[dict[str, tuple[str, int, int]]] = {
    "uint8": ("<B", 0, 0xFF),
    "int16_le": ("<h", -0x8000, 0x7FFF),
    "uint16_le": ("<H", 0, 0xFFFF),
    "uint32_le": ("<I", 0, 0xFFFFFFFF),
    "uint64_le": ("<Q", 0, 0xFFFFFFFFFFFFFFFF),
}


class ProtocolError(Exception):
    """Base error for an invalid or unsupported protocol artifact."""


class ManifestError(ProtocolError):
    """The protocol manifest is internally inconsistent."""


class DecodeError(ProtocolError):
    """A frame cannot be decoded according to the manifest."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class UnknownProfileError(DecodeError):
    """The gateway reported a telemetry profile this client does not know."""

    def __init__(self, profile_id: int) -> None:
        self.profile_id = profile_id
        super().__init__("unknown_profile")


@dataclass(frozen=True, slots=True)
class DecodedTelemetry:
    """Decoded telemetry values for one profile."""

    profile_id: int
    profile_name: str
    raw: dict[str, int | bytes | str]
    values: dict[str, int | float | bytes | str | None]


@dataclass(frozen=True, slots=True)
class DecodedStreamMessage:
    """A known gateway stream message."""

    kind: int
    name: str
    fields: dict[str, int | bytes | str]


@dataclass(frozen=True, slots=True)
class UnknownStreamMessage:
    """A version-compatible message kind that the client must ignore."""

    stream_version: int
    kind: int
    sequence: int
    data: bytes


class ProtocolManifest:
    """Validated, immutable-by-convention view of the protocol manifest."""

    def __init__(self, document: JsonObject) -> None:
        if document.get("schema_version") != 1:
            raise ManifestError("unsupported manifest schema_version")
        if document.get("scope") != "gateway_client_telemetry":
            raise ManifestError("unexpected manifest scope")

        self.document = document
        self.telemetry: JsonObject = document["telemetry"]
        self.gateway_stream: JsonObject = document["gateway_stream"]
        self.profiles = self._index_unique(
            self.telemetry["profiles"], "id", "telemetry profile ID"
        )
        self.stream_messages = self._index_unique(
            self.gateway_stream["messages"], "kind", "stream message kind"
        )
        self._validate_telemetry_fields()

    @classmethod
    def load_default(cls) -> ProtocolManifest:
        """Load the protocol artifact vendored with the integration."""
        path = Path(__file__).with_name("protocol_data") / "protocol-manifest.json"
        with path.open(encoding="utf-8") as file:
            return cls(json.load(file))

    @staticmethod
    def _index_unique(
        items: list[JsonObject], key: str, label: str
    ) -> dict[int, JsonObject]:
        indexed: dict[int, JsonObject] = {}
        for item in items:
            value = item[key]
            if value in indexed:
                raise ManifestError(f"duplicate {label}: {value}")
            indexed[value] = item
        return indexed

    def _validate_telemetry_fields(self) -> None:
        semantics: dict[str, tuple[str | None, str | None]] = {}
        common_fields = self.telemetry["common_fields"]
        for profile in self.profiles.values():
            names: set[str] = set()
            for field in [*common_fields, *profile["fields"]]:
                name = field["name"]
                if name in names:
                    raise ManifestError(
                        f"duplicate telemetry field {name!r} in profile {profile['id']}"
                    )
                names.add(name)
                current = (field.get("quantity"), field.get("unit"))
                previous = semantics.setdefault(name, current)
                if previous != current:
                    raise ManifestError(
                        f"telemetry field {name!r} changes quantity or unit"
                    )

    def decode_telemetry(self, profile_id: int, data: bytes) -> DecodedTelemetry:
        """Decode and validate one complete v2 radio telemetry frame."""
        profile = self.profiles.get(profile_id)
        if profile is None:
            raise UnknownProfileError(profile_id)
        if not data or data[0] != self.telemetry["header"]:
            raise DecodeError("wrong_header")
        if len(data) != profile["frame_size"]:
            raise DecodeError("wrong_length")

        raw: dict[str, int | bytes | str] = {}
        values: dict[str, int | float | bytes | str | None] = {}
        for field in [*self.telemetry["common_fields"], *profile["fields"]]:
            value = self._read_field(data, field, raw)
            raw[field["name"]] = value
            values[field["name"]] = self._field_value(field, value)
        return DecodedTelemetry(profile_id, profile["name"], raw, values)

    def decode_stream(self, data: bytes) -> DecodedStreamMessage | UnknownStreamMessage:
        """Decode one binary gateway WebSocket message."""
        prefix_size = self.gateway_stream["common_prefix_size"]
        if len(data) < prefix_size:
            raise DecodeError("wrong_length")

        version = data[0]
        if version != self.gateway_stream["version"]:
            raise DecodeError("unsupported_version")
        kind = data[1]
        sequence = struct.unpack_from("<I", data, 2)[0]
        message = self.stream_messages.get(kind)
        if message is None:
            return UnknownStreamMessage(version, kind, sequence, data)

        fields: dict[str, int | bytes | str] = {}
        all_fields = [*self.gateway_stream["common_fields"], *message["fields"]]
        minimum_size = message["size"].get("fixed", message["size"].get("base"))
        if len(data) < minimum_size:
            raise DecodeError("wrong_length")
        for field in all_fields:
            fields[field["name"]] = self._read_field(data, field, fields)

        size = message["size"]
        expected_size = size.get("fixed")
        if expected_size is None:
            expected_size = size["base"] + fields[size["plus_field"]]
        if len(data) != expected_size:
            raise DecodeError("wrong_length")
        return DecodedStreamMessage(kind, message["name"], fields)

    @staticmethod
    def _read_field(
        data: bytes,
        field: JsonObject,
        decoded: dict[str, int | bytes | str],
    ) -> int | bytes | str:
        encoding = field["encoding"]
        offset = field["offset"]
        if encoding == "bytes":
            length = field.get("length")
            if length is None:
                source = decoded.get(field["length_from"])
                if not isinstance(source, int):
                    raise DecodeError("invalid_value")
                length = source
            value = data[offset : offset + length]
            if len(value) != length:
                raise DecodeError("wrong_length")
            if field.get("format") == "lowercase_hex":
                return value.hex()
            return value

        try:
            return struct.unpack_from(_INTEGER_TYPES[encoding][0], data, offset)[0]
        except (KeyError, struct.error) as error:
            raise DecodeError("wrong_length") from error

    @staticmethod
    def _field_value(
        field: JsonObject, raw: int | bytes | str
    ) -> int | float | bytes | str | None:
        if not isinstance(raw, int):
            return raw
        encoding = field["encoding"]
        type_min, type_max = _INTEGER_TYPES[encoding][1:]
        sentinel = field.get("no_value_raw")
        if sentinel == "type_min":
            sentinel = type_min
        elif sentinel == "type_max":
            sentinel = type_max
        if sentinel is not None and raw == sentinel:
            return None
        if "allowed_raw" in field and raw not in field["allowed_raw"]:
            raise DecodeError("invalid_value")
        if "valid_raw" in field:
            limits = field["valid_raw"]
            if not limits["min"] <= raw <= limits["max"]:
                raise DecodeError("invalid_value")
        if "scale" in field:
            scale = field["scale"]
            return raw * scale["numerator"] / scale["denominator"]
        return raw
