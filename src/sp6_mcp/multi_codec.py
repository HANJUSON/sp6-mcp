"""Evidence-gated codec for SP6 Multi payloads.

Only the fields listed in ``FIELDS`` are interpreted. Each one records how it
was established (see ``docs/protocol/multi-fields.md``). Fields marked
``writable=False`` are reported when reading but are never changed by the
builder; requests to change them fail with ``UNSUPPORTED_FIELD``.

Payload layout as observed (all offsets in bytes):

* ``0..19``  name, printable ASCII, space padded; the instrument allows 16
  characters (Musician's Guide 4-11), the remaining 4 bytes are spaces in every
  observed Multi.
* ``20..22`` the literal ``PC3`` format tag.
* zone blocks: exactly four variable-length blocks, each starting with the
  marker ``03 1B <state> <channel> FF 07``. Offsets below are relative to the
  start of that marker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .errors import UNSUPPORTED_FIELD, UNSUPPORTED_FORMAT, INVALID_SPEC, Sp6Error

CODEC_VERSION = "multi-codec/0.1"

NAME_BYTES = 20
NAME_MAX_CHARS = 16
FORMAT_TAG = b"PC3"
ZONE_COUNT = 4

ZONE_MARKER = re.compile(rb"\x03\x1b[\x01\x02][\x01-\x10]\xff\x07", re.S)
# Volume/Pan entry pair observed near the end of each zone block:
# 07 <volume> 7F 00 0A <pan> 40 00
_MIXER = re.compile(rb"\x07([\x00-\x7f])\x7f\x00\x0a([\x00-\x7f])\x40\x00", re.S)


@dataclass(frozen=True)
class FieldDef:
    name: str
    offset: int | None  # relative to zone marker; None = located by pattern
    writable: bool
    evidence: str


FIELDS: dict[str, FieldDef] = {
    "enabled": FieldDef(
        "enabled", 2, True,
        "1050 hardware readback: zones 1-2 active (02), zone 3/4 inactive (01).",
    ),
    "midi_channel": FieldDef(
        "midi_channel", 3, True,
        "1050 readback: zone 3 created on channel 3 (03); factory Multis use 1..4.",
    ),
    "program_id": FieldDef(
        "program_id", 12, True,
        "Two 7-bit bytes (hi*128+lo). 1050 readback: programs 1, 67, 1; "
        "user Program IDs 1025-1194 decode consistently.",
    ),
    "key_low": FieldDef(
        "key_low", 18, True,
        "1050 edit A#3/B3 -> A#4/B4 changed zone 2 low 59->71; readback B4.",
    ),
    "key_high": FieldDef(
        "key_high", 19, True,
        "Same 1050 edit changed zone 1 high 58->70; readback A#4.",
    ),
    "transpose_semitones": FieldDef(
        "transpose_semitones", 21, True,
        "Signed 16-bit big endian. 1050 strings edit 0 -> -12 (FF F4); readback -12.",
    ),
    "volume": FieldDef(
        "volume", None, False,
        "07 <v> 7F 00 pattern; 1050 zones read 115 as configured. Single Multi of "
        "evidence, so read-only until an isolated editor change is captured.",
    ),
    "pan": FieldDef(
        "pan", None, False,
        "0A <p> 40 00 pattern; 1050 zones read 64 (centre) as configured. Read-only.",
    ),
}

WRITABLE_FIELDS = tuple(name for name, f in FIELDS.items() if f.writable)


def decode_program_id(hi: int, lo: int) -> int:
    return (hi << 7) | lo


def encode_program_id(program_id: int) -> tuple[int, int]:
    if not 1 <= program_id <= 2048:
        raise Sp6Error(INVALID_SPEC, f"Program ID {program_id} is outside 1-2048", "program_id")
    return program_id >> 7, program_id & 0x7F


@dataclass(frozen=True)
class ZoneView:
    zone: int
    marker_offset: int
    block_end: int
    values: dict[str, Any]
    mixer_offset: int | None


@dataclass(frozen=True)
class MultiView:
    name: str
    zones: tuple[ZoneView, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "codec_version": CODEC_VERSION,
            "name": self.name,
            "zones": [{"zone": z.zone, **z.values} for z in self.zones],
            "uninterpreted": "All other bytes (velocity, controllers, arpeggiator, FX, "
            "pedals, wheels) are preserved but not interpreted.",
        }


def _check_header(payload: bytes) -> str:
    if len(payload) < NAME_BYTES + len(FORMAT_TAG):
        raise Sp6Error(UNSUPPORTED_FORMAT, "Multi payload is too short or empty")
    if payload[NAME_BYTES : NAME_BYTES + 3] != FORMAT_TAG:
        raise Sp6Error(UNSUPPORTED_FORMAT, "Multi payload does not carry the PC3 format tag")
    raw = payload[:NAME_BYTES]
    if any(c < 0x20 or c > 0x7E for c in raw):
        raise Sp6Error(UNSUPPORTED_FORMAT, "Multi name field is not printable ASCII")
    return raw.decode("ascii").rstrip(" ")


def zone_offsets(payload: bytes) -> list[int]:
    offsets = [m.start() for m in ZONE_MARKER.finditer(payload, NAME_BYTES + 3)]
    if len(offsets) != ZONE_COUNT:
        raise Sp6Error(
            UNSUPPORTED_FORMAT,
            f"Expected {ZONE_COUNT} zone blocks, found {len(offsets)}; this Multi layout is not supported",
        )
    return offsets


def decode(payload: bytes) -> MultiView:
    name = _check_header(payload)
    offsets = zone_offsets(payload)
    ends = offsets[1:] + [len(payload)]
    zones = []
    for index, (start, end) in enumerate(zip(offsets, ends), start=1):
        if end - start < 33:
            raise Sp6Error(UNSUPPORTED_FORMAT, f"Zone {index} block is too short ({end - start} bytes)")
        b = payload
        values: dict[str, Any] = {
            "enabled": b[start + 2] == 2,
            "midi_channel": b[start + 3],
            "program_id": decode_program_id(b[start + 12], b[start + 13]),
            "key_low": b[start + 18],
            "key_high": b[start + 19],
            "transpose_semitones": int.from_bytes(b[start + 21 : start + 23], "big", signed=True),
        }
        mixer = _MIXER.search(b, start, end)
        if mixer:
            values["volume"] = mixer[1][0]
            values["pan"] = mixer[2][0]
        else:
            values["volume"] = None
            values["pan"] = None
        zones.append(ZoneView(index, start, end, values, mixer.start() if mixer else None))
    return MultiView(name, tuple(zones))


def validate_value(field: str, value: Any) -> None:
    def bad(msg: str) -> Sp6Error:
        return Sp6Error(INVALID_SPEC, msg, field)

    if field == "enabled":
        if not isinstance(value, bool):
            raise bad("enabled must be true or false")
    elif field == "midi_channel":
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 16:
            raise bad("midi_channel must be 1-16")
    elif field == "program_id":
        if not isinstance(value, int) or isinstance(value, bool):
            raise bad("program_id must be an integer")
        encode_program_id(value)
    elif field in ("key_low", "key_high"):
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 127:
            raise bad(f"{field} must be MIDI 0-127")
    elif field == "transpose_semitones":
        # Observed range in editor data is -36..+24. The device limit is not
        # verified, so stay within the observed envelope.
        if not isinstance(value, int) or isinstance(value, bool) or not -36 <= value <= 24:
            raise bad("transpose_semitones must be -36..24 (observed envelope)")


def encode_name(name: str) -> bytes:
    if not isinstance(name, str) or not name.strip():
        raise Sp6Error(INVALID_SPEC, "Name must be a non-empty string", "name")
    if len(name) > NAME_MAX_CHARS:
        raise Sp6Error(INVALID_SPEC, f"Name exceeds {NAME_MAX_CHARS} characters", "name")
    if any(ord(c) < 0x20 or ord(c) > 0x7E for c in name):
        raise Sp6Error(INVALID_SPEC, "Name must be printable ASCII; no lossy conversion is done", "name")
    return name.encode("ascii").ljust(NAME_BYTES, b" ")


def apply(payload: bytes, name: str | None, zone_changes: dict[int, dict[str, Any]]) -> bytes:
    """Return a new payload with only verified fields changed.

    ``zone_changes`` maps zone number (1-4) to {field: value}. Unknown or
    read-only fields raise ``UNSUPPORTED_FIELD`` before any byte is changed.
    """
    view = decode(payload)
    for zone, changes in zone_changes.items():
        if zone not in range(1, ZONE_COUNT + 1):
            raise Sp6Error(INVALID_SPEC, f"Zone {zone} is outside 1-4", f"zones[{zone}]")
        for field, value in changes.items():
            fdef = FIELDS.get(field)
            if fdef is None or not fdef.writable:
                raise Sp6Error(
                    UNSUPPORTED_FIELD,
                    f"Field {field!r} cannot be written by {CODEC_VERSION}",
                    f"zones[{zone}].{field}",
                    details={"writable_fields": list(WRITABLE_FIELDS)},
                )
            validate_value(field, value)

    out = bytearray(payload)
    if name is not None:
        out[:NAME_BYTES] = encode_name(name)
    for zone, changes in zone_changes.items():
        base = view.zones[zone - 1].marker_offset
        for field, value in changes.items():
            if field == "enabled":
                out[base + 2] = 2 if value else 1
            elif field == "midi_channel":
                out[base + 3] = value
            elif field == "program_id":
                out[base + 12], out[base + 13] = encode_program_id(value)
            elif field == "key_low":
                out[base + 18] = value
            elif field == "key_high":
                out[base + 19] = value
            elif field == "transpose_semitones":
                out[base + 21 : base + 23] = value.to_bytes(2, "big", signed=True)
    result = bytes(out)
    # Re-decode to prove the result is still parseable and carries the values.
    check = decode(result)
    for zone, changes in zone_changes.items():
        for field, value in changes.items():
            if check.zones[zone - 1].values[field] != value:
                raise Sp6Error(UNSUPPORTED_FORMAT, f"Re-decode mismatch on zone {zone} {field}")
    return result


def changed_offsets(before: bytes, after: bytes) -> list[int]:
    if len(before) != len(after):
        raise Sp6Error(UNSUPPORTED_FORMAT, "Payload length changed")
    return [i for i, (a, b) in enumerate(zip(before, after)) if a != b]


def allowed_offsets(payload: bytes) -> set[int]:
    """Byte offsets the codec is allowed to touch (name + writable zone fields)."""
    view = decode(payload)
    allowed = set(range(NAME_BYTES))
    for z in view.zones:
        base = z.marker_offset
        allowed.update({base + 2, base + 3, base + 12, base + 13, base + 18, base + 19, base + 21, base + 22})
    return allowed
