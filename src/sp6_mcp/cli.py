"""Command line front end sharing the same core as the MCP server.

    sp6-mcp serve                         run the MCP server on stdio
    sp6-mcp capabilities
    sp6-mcp inspect FILE [--kind multi] [--user] [--all]
    sp6-mcp read FILE MULTI_ID
    sp6-mcp validate SHEET.json
    sp6-mcp build SHEET.json OUTPUT [--format fmlt|ksp6]
    sp6-mcp diff SOURCE ARTIFACT [--source-multi-id N]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import multi_codec as mc
from .build import build, diff_files
from .container import Container
from .errors import Sp6Error
from .spec import parse_spec
from .validate import load_template, validate
from .workspace import Workspace


def _print(obj) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def _sheet(path: str):
    return parse_spec(json.loads(Path(path).read_text(encoding="utf-8")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sp6-mcp", description="Unofficial offline SP6 Multi tools")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    sub.add_parser("capabilities")
    p = sub.add_parser("inspect")
    p.add_argument("file")
    p.add_argument("--kind", choices=["multi", "program"])
    p.add_argument("--user", action="store_true", help="only IDs 1025-2048")
    p.add_argument("--all", action="store_true", help="include empty records")
    p = sub.add_parser("read")
    p.add_argument("file")
    p.add_argument("multi_id", type=int)
    p = sub.add_parser("validate")
    p.add_argument("sheet")
    p = sub.add_parser("build")
    p.add_argument("sheet")
    p.add_argument("output")
    p.add_argument("--format", choices=["ksp6", "fmlt"], default="ksp6")
    p = sub.add_parser("diff")
    p.add_argument("source")
    p.add_argument("artifact")
    p.add_argument("--source-multi-id", type=int)
    args = parser.parse_args(argv)

    ws = Workspace.from_env()
    try:
        if args.cmd == "serve":
            from .server import main as serve

            serve()
        elif args.cmd == "capabilities":
            from .server import capabilities

            _print(capabilities())
        elif args.cmd == "inspect":
            c = Container.from_path(ws.input_path(args.file))
            lo, hi = (1025, 2048) if args.user else (None, None)
            _print({"sha256": c.sha256, "inventory": c.inventory(),
                    "objects": c.listing([args.kind] if args.kind else None, lo, hi, args.all)})
        elif args.cmd == "read":
            rec = Container.from_path(ws.input_path(args.file)).get("multi", args.multi_id)
            if rec is None or rec.empty:
                raise Sp6Error("INVALID_SPEC", f"Multi {args.multi_id} absent or empty", "multi_id")
            _print(mc.decode(rec.payload).to_dict())
        elif args.cmd == "validate":
            spec = _sheet(args.sheet)
            report = validate(spec, load_template(spec, ws.input_path(spec.template.source_path)))
            _print(report.to_dict())
            return 0 if report.valid else 1
        elif args.cmd == "build":
            _print(build(_sheet(args.sheet), ws, args.output, args.format).report)
        elif args.cmd == "diff":
            _print(diff_files(ws.input_path(args.source), Path(args.artifact), args.source_multi_id))
    except Sp6Error as exc:
        _print({"status": "error", "error": exc.to_dict()})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
