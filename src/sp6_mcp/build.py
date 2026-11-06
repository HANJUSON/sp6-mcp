"""Create a new Multi file from a validated sheet, and compare files.

Nothing here touches an instrument. The result's ``phase`` is at most
``file_built`` ("파일 생성됨"); loading into the editor, sending and saving
are separate, later states (README section 11).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from . import __version__
from . import multi_codec as mc
from .container import Container, Record, sha256, single_object_export
from .errors import INVALID_SPEC, SLOT_OCCUPIED, UNSUPPORTED_FORMAT, Sp6Error
from .spec import MultiSpec
from .validate import ValidationReport, load_template, validate
from .workspace import Workspace, write_new_file

OutputFormat = Literal["fmlt", "ksp6"]


@dataclass
class BuildResult:
    artifact_id: str
    output_path: Path
    manifest_path: Path
    output_sha256: str
    report: dict[str, Any]


def _zone_changes(spec: MultiSpec) -> dict[int, dict[str, Any]]:
    changes: dict[int, dict[str, Any]] = {}
    for z in spec.zones:
        provided = {k: v for k, v in z.provided().items() if k in mc.WRITABLE_FIELDS}
        if provided:
            changes[z.zone] = provided
    return changes


def _program_records(container: Container, payload: bytes) -> list[Record]:
    """Programs referenced by the new Multi that exist in the template file."""
    out: list[Record] = []
    seen: set[int] = set()
    for zone in mc.decode(payload).zones:
        pid = zone.values["program_id"]
        if pid in seen:
            continue
        seen.add(pid)
        rec = container.get("program", pid)
        # User Programs are left out so an editor import can never carry a
        # stale copy over a Program saved on the keyboard.
        if rec is not None and not rec.empty and pid < 1025:
            out.append(rec)
    return out


def build(
    spec: MultiSpec,
    workspace: Workspace,
    output_path: str,
    output_format: OutputFormat = "ksp6",
) -> BuildResult:
    template_path = workspace.input_path(spec.template.source_path)
    template = load_template(spec, template_path)
    report: ValidationReport = validate(spec, template)
    if not report.can_build:
        first = report.errors[0] if report.errors else None
        raise Sp6Error(
            first.code if first else INVALID_SPEC,
            "Sheet is not buildable; see details.validation",
            first.field if first else None,
            details={"validation": report.to_dict()},
        )

    if output_format not in ("fmlt", "ksp6"):
        raise Sp6Error(INVALID_SPEC, f"Unknown output format {output_format}", "output_format")
    if Path(output_path).suffix.lower() != f".{output_format}":
        raise Sp6Error(INVALID_SPEC, f"output_path must end with .{output_format} for this format", "output_path")
    target = workspace.output_path(output_path, protect=(template_path,))
    new_payload = mc.apply(template.payload, spec.name, _zone_changes(spec))
    target_id = spec.preferred_user_multi_id
    container = template.container

    if output_format == "ksp6":
        existing = container.get("multi", target_id)
        if existing is None:
            raise Sp6Error(UNSUPPORTED_FORMAT, f"Template file has no record slot {target_id} to fill", "preferred_user_multi_id")
        if not existing.empty:
            raise Sp6Error(SLOT_OCCUPIED, f"Record {target_id} is not empty in the template file", "preferred_user_multi_id")
        data = container.encode({("multi", target_id): new_payload})
        included_programs: list[int] = []
    elif output_format == "fmlt":
        programs = _program_records(container, new_payload)
        multi_record = Record("multi", target_id, new_payload, 0, 0)
        data = single_object_export(multi_record, programs, target_id)
        included_programs = [p.object_id for p in programs]
    else:  # pragma: no cover - checked above
        raise AssertionError(output_format)

    verification = verify_artifact(container, spec.template.id, data, target_id, output_format)

    out_sha = sha256(data)
    artifact_id = f"art-{out_sha[:16]}"
    manifest_path = target.with_name(target.name + ".manifest.json")
    if manifest_path.exists():
        raise Sp6Error("OUTPUT_PATH_DENIED", "Manifest path already exists", "output_path")
    result_report = {
        "artifact_id": artifact_id,
        "phase": "file_built",
        "next_phases": ["file_parsed_by_editor", "sent", "saved_and_read_back", "played"],
        "output": {"path": str(target), "format": output_format, "bytes": len(data), "sha256": out_sha},
        "target_multi_id": target_id,
        "included_program_ids": included_programs,
        "template": report.template,
        "codec_version": mc.CODEC_VERSION,
        "tool_version": __version__,
        "changes": verification["field_changes"],
        "preserved": verification["preserved"],
        "dependencies": report.dependencies,
        "warnings": [i.to_dict() for i in report.issues if i.severity == "warning"] + (
            [{
                "severity": "warning",
                "code": "PARTIAL_EXPORT",
                "field": "output_format",
                "message": "Editor .fmlt exports also carry each Program's FX and algorithm records; this file "
                "contains only the Multi and Program records. Prefer output_format 'ksp6' until the editor "
                "has been shown to accept this file.",
            }] if output_format == "fmlt" else []
        ),
        "effective_zones": report.effective_zones,
        "not_adjusted": "Velocity, volume, pan, controllers (ModWheel/FILTER1), Arp button, pedals, wheels "
        "and FX keep the template's values; this codec cannot write them yet.",
        "send_instructions": [
            "Open the file in the SP6 editor and check the Multi shows the expected zones.",
            "Receive the instrument's User Multis first; the editor shows all slots empty until you do.",
            "Send ONLY this one Multi to a slot confirmed empty on the instrument. Never use Send All / bank send.",
            "Program records in the file are references for the editor; do not send them over existing Programs.",
        ],
        "device_write_state": "not_sent",
    }
    manifest = {"sheet": spec.model_dump(mode="json"), "result": result_report}
    try:
        write_new_file(target, data)
        write_new_file(manifest_path, (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    except Sp6Error:
        raise
    except OSError as exc:
        raise Sp6Error("OUTPUT_PATH_DENIED", f"Could not write output: {exc}", "output_path") from None
    result_report["manifest_path"] = str(manifest_path)
    return BuildResult(artifact_id, target, manifest_path, out_sha, result_report)


def verify_artifact(
    template: Container, template_multi_id: int, data: bytes, target_id: int, output_format: str
) -> dict[str, Any]:
    """Re-parse the generated bytes and prove what changed and what did not."""
    out = Container(data)
    new = out.get("multi", target_id)
    if new is None or new.empty:
        raise Sp6Error(UNSUPPORTED_FORMAT, "Generated file lost the target Multi")
    original = template.get("multi", template_multi_id)
    assert original is not None
    offsets = mc.changed_offsets(original.payload, new.payload)
    outside = sorted(set(offsets) - mc.allowed_offsets(original.payload))
    if outside:
        raise Sp6Error(UNSUPPORTED_FORMAT, f"Generated Multi differs outside codec fields at {outside[:5]}")
    field_changes = compare_multis(original.payload, new.payload)
    preserved: dict[str, Any] = {"multi_bytes_outside_codec_fields_identical": True}
    if output_format == "ksp6":
        others_same = all(
            out.get(r.kind, r.object_id) is not None and out.get(r.kind, r.object_id).payload == r.payload
            for r in template.records
            if (r.kind, r.object_id) != ("multi", target_id)
        )
        same_ids = {(r.kind, r.object_id) for r in out.records} == {(r.kind, r.object_id) for r in template.records}
        # Non-record bytes: re-encode the template with the same replacement and compare.
        identical_rest = template.encode({("multi", target_id): new.payload}) == data
        if not (others_same and same_ids and identical_rest):
            raise Sp6Error(UNSUPPORTED_FORMAT, "Generated bank changed records other than the target")
        preserved.update(other_records_identical=True, non_record_bytes_identical=True)
    else:
        for rec in out.records:
            if rec.kind == "program":
                src = template.get("program", rec.object_id)
                if src is None or src.payload != rec.payload:
                    raise Sp6Error(UNSUPPORTED_FORMAT, f"Program {rec.object_id} differs from the template file")
        preserved.update(program_records_copied_verbatim=True)
    return {"field_changes": field_changes, "preserved": preserved}


def compare_multis(a: bytes, b: bytes) -> dict[str, Any]:
    va, vb = mc.decode(a), mc.decode(b)
    zones = []
    for za, zb in zip(va.zones, vb.zones):
        diffs = {k: {"from": za.values[k], "to": zb.values[k]} for k in za.values if za.values[k] != zb.values.get(k)}
        if diffs:
            zones.append({"zone": za.zone, "fields": diffs})
    out: dict[str, Any] = {"zones": zones}
    if va.name != vb.name:
        out["name"] = {"from": va.name, "to": vb.name}
    if len(a) == len(b):
        uninterpreted = sorted(set(mc.changed_offsets(a, b)) - mc.allowed_offsets(a))
        out["uninterpreted_bytes_changed"] = len(uninterpreted)
    else:
        out["uninterpreted_bytes_changed"] = "length differs"
    return out


def diff_files(source: Path, artifact: Path, source_multi_id: int | None = None) -> dict[str, Any]:
    """Record-level and field-level comparison of two container files."""
    a, b = Container.from_path(source), Container.from_path(artifact)
    ids_a = {(r.kind, r.object_id): r for r in a.records}
    ids_b = {(r.kind, r.object_id): r for r in b.records}
    added, removed, changed, same = [], [], [], 0
    for key, rb in ids_b.items():
        ra = ids_a.get(key)
        if ra is None:
            added.append(key)
        elif ra.payload != rb.payload:
            changed.append(key)
        else:
            same += 1
    removed = [k for k in ids_a if k not in ids_b]
    result: dict[str, Any] = {
        "source": {"file": source.name, "sha256": a.sha256},
        "artifact": {"file": artifact.name, "sha256": b.sha256},
        "records_identical": same,
        "records_added": [f"{k}:{i}" for k, i in added],
        "records_removed_count": len(removed),
        "records_changed": [f"{k}:{i}" for k, i in changed],
        "multi_field_diffs": [],
    }
    src = None
    if source_multi_id is not None:
        src = a.get("multi", source_multi_id)
        if src is None or src.empty:
            raise Sp6Error(INVALID_SPEC, f"Multi {source_multi_id} not in source", "source_multi_id")
    pairs = []
    for k in changed:
        if k[0] != "multi":
            continue
        # A slot that was empty in the source is compared with the template Multi.
        left = src if (src is not None and ids_a[k].empty) else ids_a[k]
        pairs.append((left, ids_b[k]))
    if src is not None:
        pairs += [(src, ids_b[k]) for k in added if k[0] == "multi" and not ids_b[k].empty]
    for ra, rb in pairs:
        if ra.empty or rb.empty:
            result["multi_field_diffs"].append({"multi_id": rb.object_id, "note": "empty payload on one side"})
            continue
        try:
            result["multi_field_diffs"].append(
                {"from_multi_id": ra.object_id, "to_multi_id": rb.object_id, **compare_multis(ra.payload, rb.payload)}
            )
        except Sp6Error as exc:
            result["multi_field_diffs"].append({"multi_id": rb.object_id, "error": exc.to_dict()})
    return result
