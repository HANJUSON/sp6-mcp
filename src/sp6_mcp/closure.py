"""Dependency closure for single-Multi ``.fmlt`` exports.

Editor ``.fmlt`` exports observed so far (see ``docs/protocol/multi-fields.md``)
are laid out as::

    Multi record
    for each zone in order (enabled or not):
        Program record
        FX record for each non-zero FX slot of the Program, in slot order
        Alg record for each layer of the Program, in layer order (not de-duplicated)

FX and Alg records are copied byte for byte from the bank file the Multi came
from. Nothing here interprets FX or algorithm contents.

Program layout facts used (offsets relative to the Program payload, which
starts with the 20-byte name):

* ``20..22`` ``PC3`` tag.
* FX slots at 105, 117, 129: ``03 03 <51|52|53> <flags> <u16 BE FX id>``.
* layers: a ``03 03 51`` marker at ``521 + 318*k``; the layer's algorithm ID is
  the byte 112 bytes before that marker. The byte before it only ever held
  0, 2 or 4 in the corpus, so it is treated as flags and IDs as one byte
  (the bank's algorithm table tops out at 148).
* the grid slot after the last layer never starts with ``03 03``; if it does,
  the Program is refused because a layer of an unknown kind may be there.

All 1,643 non-empty Programs in the private backups match this grid; anything
that does not is refused rather than guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .container import Container
from .errors import DEPENDENCY_MISMATCH, UNSUPPORTED_FORMAT, Sp6Error

FX_SLOTS = (105, 117, 129)
FX_CODES = (0x51, 0x52, 0x53)
LAYER_FIRST = 521
LAYER_STRIDE = 318
ALG_BACK = 112

AUX_ENVELOPES: dict[str, tuple[bytes, bytes]] = {
    # kind: (prefix, suffix); the 4-digit ID follows the prefix and the suffix.
    "fx": (b"FX***Data>", b"<FX***Data"),
    "alg": (b"Alg**Data>", b"<Alg**Data"),
}
_AUX_PATTERNS = {
    kind: re.compile(re.escape(prefix) + rb"(\d{4})(.*?)" + re.escape(suffix) + rb"\1", re.S)
    for kind, (prefix, suffix) in AUX_ENVELOPES.items()
}


# Bank files wrap the tables in these regions; single-object files do not.
AUX_REGIONS: dict[str, tuple[bytes, bytes]] = {
    "fx": (b"FX***Bank>", b"<FX***Bank"),
    "alg": (b"Alg**Bank>", b"<Alg**Bank"),
}


def _region(data: bytes, kind: str) -> tuple[int, int]:
    start_tag, end_tag = AUX_REGIONS[kind]
    start = data.find(start_tag)
    if start < 0:
        return 0, len(data)
    end = data.find(end_tag, start)
    if end < 0 or data.find(start_tag, start + 1) >= 0:
        raise Sp6Error(UNSUPPORTED_FORMAT, f"{kind} table region is not well formed")
    return start, end + len(end_tag)


def aux_records(data: bytes) -> dict[tuple[str, int], bytes]:
    """FX and Alg records keyed by (kind, id), as raw bytes.

    In bank files only the FX / Alg table regions are scanned, so bytes inside
    other records can never be mistaken for a table entry.
    """
    out: dict[tuple[str, int], bytes] = {}
    for kind, pattern in _AUX_PATTERNS.items():
        lo, hi = _region(data, kind)
        for match in pattern.finditer(data, lo, hi):
            key = (kind, int(match[1]))
            raw = match[0]
            if key in out and out[key] != raw:
                raise Sp6Error(UNSUPPORTED_FORMAT, f"Two different {kind} records with ID {key[1]}")
            out[key] = raw
    return out


@dataclass
class ProgramRefs:
    fx_ids: list[int]
    alg_ids: list[int]
    notes: list[str] = field(default_factory=list)


def program_refs(payload: bytes, program_id: int) -> ProgramRefs:
    if len(payload) < LAYER_FIRST + 3 or payload[20:23] != b"PC3":
        raise Sp6Error(UNSUPPORTED_FORMAT, f"Program {program_id} payload layout is not recognised")
    fx_ids: list[int] = []
    codes: list[int] = []
    for off in FX_SLOTS:
        if payload[off : off + 2] != b"\x03\x03" or payload[off + 2] not in FX_CODES:
            raise Sp6Error(UNSUPPORTED_FORMAT, f"Program {program_id} has no FX slot marker at {off}")
        codes.append(payload[off + 2])
        fx_id = int.from_bytes(payload[off + 4 : off + 6], "big")
        if fx_id:
            fx_ids.append(fx_id)
    notes = []
    if tuple(codes) != FX_CODES:
        notes.append(
            f"Program {program_id} FX slots appear in order {[hex(c) for c in codes]}; the editor's record order "
            "for this case has not been observed (records are emitted in slot position order)."
        )
    if len(set(fx_ids)) != len(fx_ids):
        notes.append(f"Program {program_id} uses the same FX ID in two slots; editor handling not observed.")

    markers = [m.start() for m in re.finditer(rb"\x03\x03\x51", payload) if m.start() > FX_SLOTS[-1]]
    expected = [LAYER_FIRST + LAYER_STRIDE * k for k in range(len(markers))]
    if not markers or markers != expected:
        raise Sp6Error(UNSUPPORTED_FORMAT, f"Program {program_id} layer markers do not follow the known layout")
    after = markers[-1] + LAYER_STRIDE
    if payload[after : after + 2] == b"":
        raise Sp6Error(UNSUPPORTED_FORMAT, f"Program {program_id} may have a layer of an unknown kind after layer {len(markers)}")
    alg_ids = [payload[x - ALG_BACK] for x in markers]
    return ProgramRefs(fx_ids, alg_ids, notes)


@dataclass
class FmltExport:
    data: bytes
    program_ids: list[int]
    excluded_program_ids: list[int]
    missing_program_ids: list[int]
    fx_ids: dict[int, list[int]]
    alg_ids: dict[int, list[int]]
    notes: list[str]

    @property
    def complete(self) -> bool:
        return not self.excluded_program_ids and not self.missing_program_ids


def export_fmlt(
    bank: Container,
    zone_program_ids: list[int],
    multi_record: bytes,
    *,
    aux: dict[tuple[str, int], bytes] | None = None,
) -> FmltExport:
    """Assemble a single-Multi export from ``bank``.

    ``multi_record`` is the encoded Multi record. User Programs (1025+) are
    always left out, so an editor import cannot carry a stale copy over a
    Program saved on the keyboard.
    """
    aux = aux if aux is not None else aux_records(bank.data)
    parts = [multi_record]
    included: list[int] = []
    excluded: list[int] = []
    missing: list[int] = []
    fx_map: dict[int, list[int]] = {}
    alg_map: dict[int, list[int]] = {}
    notes: list[str] = []
    seen: set[int] = set()
    repeated: set[int] = set()
    for pid in zone_program_ids:
        if pid in seen:
            if pid not in repeated:
                repeated.add(pid)
                notes.append(
                    f"Program {pid} is used by more than one zone; it is written once. The editor's handling of "
                    "repeated Programs has not been observed."
                )
            continue
        seen.add(pid)
        if pid >= 1025:
            excluded.append(pid)
            continue
        rec = bank.get("program", pid)
        if rec is None or rec.empty:
            missing.append(pid)
            continue
        refs = program_refs(rec.payload, pid)
        notes.extend(refs.notes)
        chunk = [rec.encode()]
        for kind, ids in (("fx", refs.fx_ids), ("alg", refs.alg_ids)):
            for ref in ids:
                raw = aux.get((kind, ref))
                if raw is None:
                    raise Sp6Error(
                        DEPENDENCY_MISMATCH,
                        f"Program {pid} needs {kind} record {ref}, which is not in the bank file",
                    )
                chunk.append(raw)
        parts.extend(chunk)
        included.append(pid)
        fx_map[pid] = refs.fx_ids
        alg_map[pid] = refs.alg_ids
    return FmltExport(b"".join(parts), included, excluded, missing, fx_map, alg_map, notes)
