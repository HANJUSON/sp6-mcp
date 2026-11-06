"""Check the codec against local, private backup files.

Usage:  python scripts/verify_local_backups.py FILE [FILE ...]

Reads only. Prints a JSON summary to stdout (hashes, counts, round-trip
results, decode statistics). Do not commit the output if it contains
personal Multi names; redirect it into ``private/`` which is git-ignored.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from sp6_mcp.container import Container
from sp6_mcp.errors import Sp6Error


def check(path: Path) -> dict:
    data = path.read_bytes()
    container = Container(data)  # raises if the round trip fails
    report: dict = {
        "file": path.name,
        "bytes": len(data),
        "sha256": container.sha256,
        "roundtrip_identical": container.encode() == data,
        "inventory": {k: {kk: (len(vv) if isinstance(vv, list) else vv) for kk, vv in v.items()}
                      for k, v in container.inventory().items()},
    }
    try:
        from sp6_mcp import multi_codec
    except ImportError:  # codec not present in this version
        return report
    outcomes: Counter = Counter()
    for record in container.of_kind("multi"):
        if record.empty:
            continue
        try:
            view = multi_codec.decode(record.payload)
            # A no-op apply must return identical bytes.
            if multi_codec.apply(record.payload, None, {}) != record.payload:
                outcomes["noop_apply_changed_bytes"] += 1
            # Rewriting every zone with its own decoded values must be a no-op.
            same = {z.zone: {f: z.values[f] for f in multi_codec.WRITABLE_FIELDS} for z in view.zones}
            if multi_codec.apply(record.payload, None, same) != record.payload:
                outcomes["self_apply_changed_bytes"] += 1
            else:
                outcomes["decoded_and_self_consistent"] += 1
        except Sp6Error as exc:
            outcomes[exc.code] += 1
    report["multi_decode"] = dict(outcomes)
    return report


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    print(json.dumps([check(Path(p)) for p in argv], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
