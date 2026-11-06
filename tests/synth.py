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


def program_payload(name="SYNTH PROG") -> bytes:
    return name.encode("ascii").ljust(20, b" ") + b"PC3" + bytes(range(50))


def bank(multis: dict[int, bytes], programs: dict[int, bytes]) -> bytes:
    """A small container: program bank, opaque middle, multi bank, opaque tail."""
    parts = [b"Prog*Bank>"]
    parts += [encode_record("program", i, p) for i, p in sorted(programs.items())]
    parts.append(b"<Prog*Bank FX***Data>0001\x01\x02\x03<FX***Data0001 Set**Bank>")
    parts += [encode_record("multi", i, p) for i, p in sorted(multis.items())]
    parts.append(b"<Set**Bank\x00\xff trailing-unknown")
    return b"".join(parts)


def standard_bank() -> bytes:
    multis = {1: multi_payload("FACTORY ONE"), 1025: multi_payload("USER A"), 1026: b"", 1027: b""}
    programs = {1: program_payload("Piano"), 87: program_payload("Pad"), 1200: program_payload("Lead"), 1201: b""}
    return bank(multis, programs)
