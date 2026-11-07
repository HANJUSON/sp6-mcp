"""MCP stdio server exposing the offline SP6 tools.

Only implemented tools are registered (README FR-12). Device discovery,
receive and write tools are reported by ``sp6_capabilities`` as not
implemented and are not listed.

stdout carries MCP messages only; nothing in this module prints.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__
from . import multi_codec as mc
from .build import build, diff_files
from .container import Container
from .errors import INVALID_SPEC, Sp6Error
from .spec import parse_spec, spec_json_schema
from .validate import load_template, validate
from .workspace import Workspace

INSTRUCTIONS = """\
Offline tools for planning Kurzweil SP6 Multis (unofficial; no MIDI, no device access).
Typical flow: sp6_capabilities -> sp6_file_inspect (find template + empty IDs) ->
sp6_multi_read (template) -> sp6_multi_validate (sheet) -> sp6_multi_build -> tell the user
how to send ONLY that Multi from the editor. Never claim anything was sent or saved.
Notes use C4 = MIDI 60. Omit fields you cannot write; they keep the template value.
"""

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITES_NEW_FILE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)


def _ok(result: Any, *, phase: str | None = None, warnings: list | None = None, evidence: Any = None) -> dict:
    return {
        "status": "ok",
        "operation_id": str(uuid.uuid4()),
        "phase": phase,
        "warnings": warnings or [],
        "evidence": evidence,
        "result": result,
    }


def _fail(exc: Exception) -> ToolError:
    if not isinstance(exc, Sp6Error):
        exc = Sp6Error("UNSUPPORTED_FORMAT" if isinstance(exc, ValueError) else "IO_ERROR", f"{type(exc).__name__}: {exc}")
    return ToolError(json.dumps({"status": "error", "error": exc.to_dict()}, ensure_ascii=False))


def capabilities() -> dict:
    return {
        "server": "sp6-mcp",
        "version": __version__,
        "codec_version": mc.CODEC_VERSION,
        "unofficial": True,
        "file_formats": {
            "read": [".ksp6 (editor bank)", ".fmlt (single Multi export)"],
            "write": ["ksp6 (default: template bank with one empty record filled)", "fmlt (editor layout: Multi, then each zone's Program with its FX and algorithm records; user Programs left out)"],
        },
        "multi_fields": {
            name: {"writable": f.writable, "evidence": f.evidence} for name, f in mc.FIELDS.items()
        },
        "not_interpreted": ["velocity range", "volume/pan writes", "controllers (ModWheel, FILTER1, entry values)",
                            "arp button", "pedals", "wheels", "FX", "Program editing"],
        "device": {
            "discover": "not_implemented",
            "receive": "not_implemented",
            "write": "not_implemented",
            "note": "Send generated files with the SP6 editor yourself: one Multi to a slot confirmed empty on "
            "the keyboard; never Send All.",
        },
        "tools": [
            "sp6_capabilities", "sp6_spec_schema", "sp6_file_inspect", "sp6_object_find", "sp6_multi_read",
            "sp6_multi_validate", "sp6_multi_build", "sp6_artifact_diff",
        ],
    }


def create_server(workspace: Workspace | None = None) -> MCPServer:
    ws = workspace or Workspace.from_env()
    server = MCPServer("sp6-mcp", version=__version__, instructions=INSTRUCTIONS)

    @server.tool(annotations=READ_ONLY)
    def sp6_capabilities() -> dict:
        """What this server can and cannot do: file formats, writable Multi fields with evidence, device support."""
        return _ok(capabilities())

    @server.tool(annotations=READ_ONLY)
    def sp6_spec_schema() -> dict:
        """JSON Schema of the setting sheet accepted by sp6_multi_validate / sp6_multi_build."""
        return _ok({"schema": spec_json_schema(), "writable_zone_fields": list(mc.WRITABLE_FIELDS)})

    @server.tool(annotations=READ_ONLY)
    def sp6_file_inspect(
        path: str,
        kind: Literal["multi", "program", "all"] = "all",
        id_min: int | None = None,
        id_max: int | None = None,
        include_empty: bool = False,
        page: int = 1,
        page_size: int = 100,
    ) -> dict:
        """Hash, inventory and a paged object list of a .ksp6/.fmlt file.

        path is relative to the work root or absolute. Empty records in a file
        do NOT prove the keyboard slot is empty.
        """
        try:
            if page < 1 or not 1 <= page_size <= 500:
                raise Sp6Error(INVALID_SPEC, "page >= 1 and page_size 1-500", "page")
            file = ws.input_path(path)
            c = Container.from_path(file)
            kinds = None if kind == "all" else [kind]
            rows = c.listing(kinds, id_min, id_max, include_empty)
            start = (page - 1) * page_size
            return _ok(
                {
                    "file": file.name,
                    "bytes": len(c.data),
                    "sha256": c.sha256,
                    "inventory": c.inventory(),
                    "total": len(rows),
                    "page": page,
                    "page_size": page_size,
                    "objects": rows[start : start + page_size],
                },
                warnings=["empty_in_file means empty in this file only; receive from the keyboard before saving"],
            )
        except (Sp6Error, OSError, ValueError) as exc:
            raise _fail(exc) from None

    @server.tool(annotations=READ_ONLY)
    def sp6_object_find(path: str, query: str, kind: Literal["program", "multi"] = "program", limit: int = 20) -> dict:
        """Find Programs or Multis whose name contains query (case-insensitive) in a file.

        Use this to turn names such as "Prophet Pad" into IDs. Names come only
        from the given file; factory names are not bundled with this server.
        """
        try:
            if not query.strip() or not 1 <= limit <= 200:
                raise Sp6Error(INVALID_SPEC, "query must be non-empty and limit 1-200", "query")
            c = Container.from_path(ws.input_path(path))
            needle = query.casefold().strip()
            rows = [r for r in c.listing([kind]) if r["name"] and needle in r["name"].casefold()]
            for r in rows:
                r["user"] = 1025 <= r["id"] <= 2048
            return _ok({"file_sha256": c.sha256, "total": len(rows), "matches": rows[:limit]})
        except (Sp6Error, OSError, ValueError) as exc:
            raise _fail(exc) from None

    @server.tool(annotations=READ_ONLY)
    def sp6_multi_read(path: str, multi_id: int) -> dict:
        """Decode one Multi: name and per-zone enabled/channel/program/keys/transpose (+ read-only volume/pan)."""
        try:
            c = Container.from_path(ws.input_path(path))
            rec = c.get("multi", multi_id)
            if rec is None or rec.empty:
                raise Sp6Error(INVALID_SPEC, f"Multi {multi_id} is absent or empty in this file", "multi_id")
            view = mc.decode(rec.payload).to_dict()
            for zone in view["zones"]:
                prog = c.get("program", zone["program_id"])
                zone["program_name"] = prog.name if prog is not None and not prog.empty else None
            return _ok(view, evidence={"file_sha256": c.sha256, "multi_id": multi_id})
        except (Sp6Error, OSError, ValueError) as exc:
            raise _fail(exc) from None

    @server.tool(annotations=READ_ONLY)
    def sp6_multi_validate(spec: dict[str, Any]) -> dict:
        """Check a setting sheet against its template file and report every problem at once.

        Returns valid / can_build, errors and warnings with field paths, the
        effective zones (sheet over template) and sounding note ranges.
        """
        try:
            sheet = parse_spec(spec)
            template = load_template(sheet, ws.input_path(sheet.template.source_path))
            report = validate(sheet, template).to_dict()
            return _ok(report, phase="sheet_validated" if report["valid"] else None)
        except (Sp6Error, OSError, ValueError) as exc:
            raise _fail(exc) from None

    @server.tool(annotations=WRITES_NEW_FILE)
    def sp6_multi_build(
        spec: dict[str, Any], output_path: str, output_format: Literal["ksp6", "fmlt"] = "ksp6"
    ) -> dict:
        """Write a NEW Multi file inside the artifacts directory. Never touches the template or the keyboard.

        output_path is relative to the artifacts directory, must not exist and must end with
        .ksp6 or .fmlt matching output_format. 'ksp6' (default) is a complete copy of the template
        bank with the new Multi in one empty record; 'fmlt' follows the editor layout (Multi, then each zone's Program with its FX/algorithm records); user Programs are left out and flagged PARTIAL_EXPORT.
        """
        try:
            result = build(parse_spec(spec), ws, output_path, output_format)
            return _ok(result.report, phase="file_built", warnings=result.report["warnings"])
        except (Sp6Error, OSError, ValueError) as exc:
            raise _fail(exc) from None

    @server.tool(annotations=READ_ONLY)
    def sp6_artifact_diff(source_path: str, artifact_path: str, source_multi_id: int | None = None) -> dict:
        """Compare a template/source file with a generated file (records and decoded Multi fields).

        artifact_path may be relative to the artifacts directory.
        """
        try:
            src = ws.input_path(source_path)
            art = Path(artifact_path)
            if not art.is_absolute():
                art = ws.artifacts_dir / art
            if not art.is_file():
                raise Sp6Error("SOURCE_NOT_FOUND", f"Artifact not found: {artifact_path}", "artifact_path")
            return _ok(diff_files(src, art.resolve(), source_multi_id))
        except (Sp6Error, OSError, ValueError) as exc:
            raise _fail(exc) from None

    return server


def main() -> None:
    create_server().run("stdio")


if __name__ == "__main__":
    main()
