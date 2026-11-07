"""Synthetic fixtures. No manufacturer or user data is embedded here.

The byte layout mimics only the structural features the codec relies on
(record envelopes, name field, PC3 tag, zone markers); all other bytes are
deterministic filler.
"""

from __future__ import annotations

from sp6_mcp.container import encode_record


def zone_block(zone: int, *, enabled=True, channel=None, program=1, low=0, high=127,
               transpose=0, volume=110, pan=64, filler=40) -> bytes:
    channel = zone if channel is None else channel
    head = bytearray(b"\x03\x1b")
    head += bytes([2 if enabled else 1, channel, 0xFF, 0x07, 2, 0, 12, 12, 2, 0])
    head += bytes([program >> 7, program & 0x7F, 1, 3, 1, 1, low, high, 1])
    head += transpose.to_bytes(2, "big", signed=True)
    head += bytes([1, 0, 0, 0, 0, 100, 0, 0, 1, 127, 1])
    body = bytes((zone * 7 + i) % 251 for i in range(filler))
    mixer = bytes([0x07, volume, 0x7F, 0x00, 0x0A, pan, 0x40, 0x00])
    return bytes(head) + body + mixer + bytes(10 + zone)  # zones differ in length


def multi_payload(name="SYNTH MULTI", zones=None) -> bytes:
    zones = zones or [{}, {}, {}, {}]
    header = name.encode("ascii").ljust(20, b" ") + b"PC3" + bytes(range(1, 30))
    return header + b"".join(zone_block(i + 1, **z) for i, z in enumerate(zones)) + b"\x00\x02tail"


def program_payload(name="SYNTH PROG", fx=(5001, 5002, 0), algs=(1,)) -> bytes:
    """Program with the FX-slot and layer grid described in closure.py."""
    out = bytearray(name.encode("ascii").ljust(20, b" ") + b"PC3")
    out += bytes(105 - len(out))
    for code, fx_id in zip((0x51, 0x52, 0x53), fx):
        out += bytes([3, 3, code, 0]) + fx_id.to_bytes(2, "big") + bytes(6)
    out += bytes(521 - len(out))
    for k, alg in enumerate(algs):
        if k:
            out += bytes(318 - 6)
        out[-112] = alg
        out += bytes([3, 3, 0x51, 0, 0, 0])
    return bytes(out) + bytes(40)


def fx_record(fx_id: int, name: str) -> bytes:
    i = f"{fx_id:04d}".encode()
    return b"FX***Data>" + i + b"FX*-*Data>" + name.encode().ljust(16) + bytes([1, 2, 3]) + b"<FX*-*Data<FX***Data" + i


def alg_record(alg_id: int) -> bytes:
    i = f"{alg_id:04d}".encode()
    return b"Alg**Data>" + i + bytes([1, alg_id % 4, 1, 1]) + bytes(20) + b"<Alg**Data" + i


def bank(multis: dict[int, bytes], programs: dict[int, bytes]) -> bytes:
    """A small container: program bank, opaque middle, multi bank, opaque tail."""
    parts = [b"Prog*Bank>"]
    parts += [encode_record("program", i, p) for i, p in sorted(programs.items())]
    parts.append(b"<Prog*Bank FX***Bank>")
    parts += [fx_record(i, f"FX {i}") for i in (19, 5001, 5002, 5003)]
    parts.append(b"<FX***Bank Alg**Bank>")
    parts += [alg_record(i) for i in (1, 5, 6, 120)]
    parts.append(b"<Alg**Bank Set**Bank>")
    parts += [encode_record("multi", i, p) for i, p in sorted(multis.items())]
    parts.append(b"<Set**Bank\x00\xff trailing-unknown")
    return b"".join(parts)


def standard_bank() -> bytes:
    multis = {1: multi_payload("FACTORY ONE"), 1025: multi_payload("USER A"), 1026: b"", 1027: b""}
    programs = {
        1: program_payload("Piano", fx=(5001, 5002, 5003), algs=(1, 1, 6, 5)),
        87: program_payload("Pad", fx=(19, 0, 0), algs=(6, 120)),
        1200: program_payload("Lead"),
        1201: b"",
    }
    return bank(multis, programs)
