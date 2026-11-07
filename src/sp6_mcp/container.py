"""Byte-preserving reader/writer for SoundTower-style SP6 object containers.

Supported files are the editor's ``.ksp6`` bank files and single-object exports
such as ``.fmlt``. Both are a sequence of text-delimited records::

    Set**Dat*>NNNNSet*-Dat*>  <payload>  <Set*-Dat*<Set**Dat*NNNN   (Multi)
    Prog*Dat*>NNNNProg_Dat*>  <payload>  <Prog_Dat*<Prog*Dat*NNNN   (Program)

``NNNN`` is the 4-digit object ID. Everything outside these records (bank
headers, FX and algorithm blocks, unknown data) is kept verbatim, so an
unmodified parse/encode cycle reproduces the input byte for byte.

This module never interprets payload contents; see ``multi_codec`` for that.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

from .errors import UNSUPPORTED_FORMAT, Sp6Error

Kind = Literal["program", "multi"]

ENVELOPES: dict[str, tuple[bytes, bytes, bytes]] = {
    "program": (b"Prog*Dat*>", b"Prog_Dat*>", b"<Prog_Dat*<Prog*Dat*"),
    "multi": (b"Set**Dat*>", b"Set*-Dat*>", b"<Set*-Dat*<Set**Dat*"),
}

# NFR-07: refuse unreasonably large inputs instead of reading them into memory.
MAX_FILE_BYTES = 64 * 1024 * 1024
NAME_FIELD_BYTES = 20
USER_ID_RANGE = range(1025, 2049)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Record:
    kind: str
    object_id: int
    payload: bytes
    start: int
    end: int

    @property
    def empty(self) -> bool:
        return not self.payload

    @property
    def name(self) -> str | None:
        """Display name from the fixed-width name field, if it is printable ASCII."""
        if len(self.payload) < NAME_FIELD_BYTES:
            return None
        raw = self.payload[:NAME_FIELD_BYTES]
        if any(c < 0x20 or c > 0x7E for c in raw):
            return None
        return raw.decode("ascii").rstrip(" ")

    @property
    def is_user(self) -> bool:
        return self.object_id in USER_ID_RANGE

    def encode(self) -> bytes:
        return encode_record(self.kind, self.object_id, self.payload)


def encode_record(kind: str, object_id: int, payload: bytes) -> bytes:
    if kind not in ENVELOPES:
        raise Sp6Error(UNSUPPORTED_FORMAT, f"Unknown record kind {kind!r}")
    if not 0 <= object_id <= 9999:
        raise Sp6Error(UNSUPPORTED_FORMAT, f"Object ID {object_id} does not fit 4 digits")
    prefix, middle, suffix = ENVELOPES[kind]
    if any(m in payload for env in ENVELOPES.values() for m in (env[0], env[2])):
        raise Sp6Error(UNSUPPORTED_FORMAT, "Payload contains a record envelope")
    ident = f"{object_id:04d}".encode("ascii")
    return prefix + ident + middle + payload + suffix + ident


def _pattern(kind: str) -> re.Pattern[bytes]:
    prefix, middle, suffix = ENVELOPES[kind]
    return re.compile(
        re.escape(prefix) + rb"(\d{4})" + re.escape(middle) + rb"(.*?)" + re.escape(suffix) + rb"\1",
        re.S,
    )


_PATTERNS = {kind: _pattern(kind) for kind in ENVELOPES}


class Container:
    """Parsed view of a container. Immutable input; edits produce new bytes."""

    def __init__(self, data: bytes):
        if len(data) > MAX_FILE_BYTES:
            raise Sp6Error(UNSUPPORTED_FORMAT, f"File is larger than {MAX_FILE_BYTES} bytes")
        self.data = bytes(data)
        records: list[Record] = []
        for kind, pattern in _PATTERNS.items():
            seen: set[int] = set()
            for match in pattern.finditer(self.data):
                object_id = int(match[1])
                if object_id in seen:
                    raise Sp6Error(UNSUPPORTED_FORMAT, f"Duplicate {kind} record {object_id}")
                seen.add(object_id)
                records.append(Record(kind, object_id, match[2], match.start(), match.end()))
        markers = [m for env in ENVELOPES.values() for m in (env[0], env[2])]
        for record in records:
            if any(m in record.payload for m in markers):
                raise Sp6Error(
                    UNSUPPORTED_FORMAT,
                    f"{record.kind} {record.object_id} payload contains a record envelope; the file is damaged",
                )
        records.sort(key=lambda r: r.start)
        if not records:
            raise Sp6Error(UNSUPPORTED_FORMAT, "No Program or Multi records found")
        cursor = 0
        for record in records:
            if record.start < cursor:
                raise Sp6Error(UNSUPPORTED_FORMAT, "Overlapping records; refusing to parse")
            cursor = record.end
        self.records: tuple[Record, ...] = tuple(records)
        # Self-check: an unmodified encode must reproduce the input exactly.
        if self.encode() != self.data:
            raise Sp6Error(UNSUPPORTED_FORMAT, "Container does not round-trip; refusing to use it")

    @classmethod
    def from_path(cls, path: Path) -> "Container":
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise Sp6Error(UNSUPPORTED_FORMAT, f"File is larger than {MAX_FILE_BYTES} bytes")
        return cls(path.read_bytes())

    @property
    def sha256(self) -> str:
        return sha256(self.data)

    def get(self, kind: str, object_id: int) -> Record | None:
        for record in self.records:
            if record.kind == kind and record.object_id == object_id:
                return record
        return None

    def of_kind(self, kind: str) -> list[Record]:
        return [r for r in self.records if r.kind == kind]

    def encode(self, replacements: dict[tuple[str, int], bytes] | None = None) -> bytes:
        """Re-serialise, optionally replacing payloads keyed by (kind, id).

        Bytes outside replaced payloads are copied unchanged.
        """
        replacements = dict(replacements or {})
        parts: list[bytes] = []
        cursor = 0
        for record in self.records:
            parts.append(self.data[cursor : record.start])
            payload = replacements.pop((record.kind, record.object_id), record.payload)
            parts.append(encode_record(record.kind, record.object_id, payload))
            cursor = record.end
        parts.append(self.data[cursor:])
        if replacements:
            missing = ", ".join(f"{k}:{i}" for k, i in replacements)
            raise Sp6Error(UNSUPPORTED_FORMAT, f"No existing record for {missing}")
        return b"".join(parts)

    def inventory(self) -> dict:
        result: dict[str, dict] = {}
        for kind in ENVELOPES:
            records = self.of_kind(kind)
            user = [r for r in records if r.is_user]
            result[kind] = {
                "records": len(records),
                "nonempty_records": sum(1 for r in records if not r.empty),
                "user_records_present": len(user),
                "user_nonempty_ids": [r.object_id for r in user if not r.empty],
                # A complete user range in the file still says nothing about
                # the instrument (README 5.1 / FR-09).
                "user_range_complete_in_file": len(user) == len(USER_ID_RANGE),
            }
        return result

    def listing(
        self,
        kinds: Iterable[str] | None = None,
        id_min: int | None = None,
        id_max: int | None = None,
        include_empty: bool = False,
    ) -> list[dict]:
        wanted = set(kinds or ENVELOPES)
        rows = []
        for record in self.records:
            if record.kind not in wanted:
                continue
            if id_min is not None and record.object_id < id_min:
                continue
            if id_max is not None and record.object_id > id_max:
                continue
            if record.empty and not include_empty:
                continue
            rows.append(
                {
                    "kind": record.kind,
                    "id": record.object_id,
                    "name": record.name,
                    "bytes": len(record.payload),
                    "empty_in_file": record.empty,
                    "sha256": sha256(record.payload) if record.payload else None,
                }
            )
        return rows

