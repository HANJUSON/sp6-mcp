"""Semantic validation of a setting sheet against its template file.

Produces a report instead of raising for rule violations so that the caller
(LLM or CLI) sees every problem at once. Structural problems are raised by
``spec.parse_spec`` before this module runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import multi_codec as mc
from .container import Container, sha256
from .errors import (
    DEPENDENCY_MISMATCH,
    INVALID_SPEC,
    SLOT_OCCUPIED,
    SLOT_STATE_UNKNOWN,
    SOURCE_CHANGED,
    UNSUPPORTED_FIELD,
    Sp6Error,
)
from .spec import MultiSpec

PHYSICAL_LOW, PHYSICAL_HIGH = 21, 108  # SP6 88 keys, A0..C8
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(midi: int) -> str:
    """C4 = MIDI 60, as used by the SP6 editor."""
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


@dataclass
class Issue:
    code: str
    message: str
    field: str | None = None
    severity: str = "error"

    def to_dict(self) -> dict:
        return {"severity": self.severity, "code": self.code, "field": self.field, "message": self.message}


@dataclass
class TemplateInfo:
    path: Path
    container: Container
    payload: bytes
    view: mc.MultiView


@dataclass
class ValidationReport:
    issues: list[Issue] = field(default_factory=list)
    effective_zones: list[dict] = field(default_factory=list)
    unsupported_fields: list[str] = field(default_factory=list)
    template: dict | None = None
    dependencies: list[dict] = field(default_factory=list)

    def error(self, code: str, message: str, field: str | None = None) -> None:
        self.issues.append(Issue(code, message, field, "error"))

    def warn(self, code: str, message: str, field: str | None = None) -> None:
        self.issues.append(Issue(code, message, field, "warning"))

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def can_build(self) -> bool:
        return self.valid and not self.unsupported_fields and self.template is not None

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "can_build": self.can_build,
            "codec_version": mc.CODEC_VERSION,
            "errors": [i.to_dict() for i in self.errors],
            "warnings": [i.to_dict() for i in self.issues if i.severity == "warning"],
            "unsupported_fields": self.unsupported_fields,
            "template": self.template,
            "dependencies": self.dependencies,
            "effective_zones": self.effective_zones,
        }


def load_template(spec: MultiSpec, path: Path) -> TemplateInfo:
    container = Container.from_path(path)
    if container.sha256 != spec.template.sha256:
        raise Sp6Error(
            SOURCE_CHANGED,
            "Template file hash does not match template.sha256",
            "template.sha256",
            details={"expected": spec.template.sha256, "actual": container.sha256},
        )
    record = container.get("multi", spec.template.id)
    if record is None or record.empty:
        raise Sp6Error(INVALID_SPEC, f"Template Multi {spec.template.id} is not present in the file", "template.id")
    return TemplateInfo(path, container, record.payload, mc.decode(record.payload))


def _unsupported(spec: MultiSpec) -> list[str]:
    out = []
    for z in spec.zones:
        for name in z.provided():
            if name not in mc.WRITABLE_FIELDS:
                out.append(f"zones[{z.zone}].{name}")
        if z.allow_inverted_velocity_range:
            out.append(f"zones[{z.zone}].allow_inverted_velocity_range")
    return out


def validate(spec: MultiSpec, template: TemplateInfo | None) -> ValidationReport:
    report = ValidationReport()

    for path in _unsupported(spec):
        report.unsupported_fields.append(path)
        report.error(
            UNSUPPORTED_FIELD,
            f"{mc.CODEC_VERSION} cannot write this field yet; omit it to keep the template value",
            path,
        )

    # Effective zone values: template overlaid by the sheet's verified fields.
    zones: dict[int, dict[str, Any]] = {}
    for n in range(1, 5):
        base = dict(template.view.zones[n - 1].values) if template else {}
        zspec = spec.zone(n)
        provided = zspec.provided() if zspec else {}
        for key in mc.WRITABLE_FIELDS:
            if key in provided:
                base[key] = provided[key]
        base["zone"] = n
        base["source"] = {k: ("sheet" if k in provided else "template") for k in mc.WRITABLE_FIELDS if k in base}
        zones[n] = base

    for n, z in zones.items():
        zspec = spec.zone(n)
        prefix = f"zones[{n}]"
        if zspec is None and template is None:
            report.warn(INVALID_SPEC, "Zone not given and no template loaded; its values are unknown", prefix)
        low, high = z.get("key_low"), z.get("key_high")
        if low is not None and high is not None and low > high:
            allowed = zspec.allow_inverted_key_range if zspec else False
            from_sheet = zspec is not None and ("key_low" in zspec.provided() or "key_high" in zspec.provided())
            if from_sheet and spec.range_policy == "split" and not allowed:
                report.error(INVALID_SPEC, "key_low > key_high creates an exclusion range; set allow_inverted_key_range to confirm", f"{prefix}.key_low")
        if z.get("enabled") and low is not None and high is not None and low <= high:
            if high < PHYSICAL_LOW or low > PHYSICAL_HIGH:
                report.warn(INVALID_SPEC, "Key range lies entirely outside the 88 physical keys (21-108)", f"{prefix}.key_low")
            t = z.get("transpose_semitones") or 0
            lo_out, hi_out = max(low, PHYSICAL_LOW) + t, min(high, PHYSICAL_HIGH) + t
            if lo_out < 0 or hi_out > 127:
                report.warn(INVALID_SPEC, "Transposed notes leave MIDI 0-127 for part of the range", f"{prefix}.transpose_semitones")
            z["sounding_range"] = {
                "physical_keys": f"{note_name(max(low, PHYSICAL_LOW))}-{note_name(min(high, PHYSICAL_HIGH))}",
                "midi_notes_out": f"{max(lo_out, 0)}-{min(hi_out, 127)}",
                "note_names_out": f"{note_name(max(lo_out, 0))}-{note_name(min(hi_out, 127))}",
            }

    enabled = [z for z in zones.values() if z.get("enabled")]
    if not enabled and template is not None:
        report.warn(INVALID_SPEC, "No zone is enabled", "zones")

    # README 5.1: zones sharing a MIDI channel play the same Program.
    by_channel: dict[int, set] = {}
    for z in enabled:
        if z.get("midi_channel") is not None and z.get("program_id") is not None:
            by_channel.setdefault(z["midi_channel"], set()).add(z["program_id"])
    for channel, programs in by_channel.items():
        if len(programs) > 1:
            report.error(INVALID_SPEC, f"Enabled zones on MIDI channel {channel} use different Programs {sorted(programs)}", "zones")

    if spec.range_policy == "split":
        normal = [z for z in enabled if z.get("key_low") is not None and z.get("key_high") is not None and z["key_low"] <= z["key_high"]]
        for i, a in enumerate(normal):
            for b in normal[i + 1 :]:
                if a["key_low"] <= b["key_high"] and b["key_low"] <= a["key_high"]:
                    lo, hi = max(a["key_low"], b["key_low"]), min(a["key_high"], b["key_high"])
                    report.error(
                        INVALID_SPEC,
                        f"Zones {a['zone']} and {b['zone']} overlap on {note_name(lo)}-{note_name(hi)} "
                        "(range_policy is split; use layer for intentional overlap)",
                        f"zones[{b['zone']}].key_low",
                    )

    if template is not None:
        c = template.container
        report.template = {
            "file": template.path.name,
            "sha256": c.sha256,
            "multi_id": spec.template.id,
            "multi_name": template.view.name,
        }
        target = c.get("multi", spec.preferred_user_multi_id)
        if target is not None and not target.empty:
            report.error(
                SLOT_OCCUPIED,
                f"Multi {spec.preferred_user_multi_id} holds '{target.name}' in the template file; choose another ID",
                "preferred_user_multi_id",
            )
        else:
            report.warn(
                SLOT_STATE_UNKNOWN,
                "The template file shows this ID as empty or absent. That is not proof the keyboard slot is empty; "
                "receive the slot from the instrument before saving.",
                "preferred_user_multi_id",
            )
        seen: set[int] = set()
        for z in enabled:
            pid = z.get("program_id")
            if pid is None or pid in seen:
                continue
            seen.add(pid)
            rec = c.get("program", pid)
            dep = {"program_id": pid, "user": pid >= 1025}
            if rec is None or rec.empty:
                dep.update(present_in_file=False)
                report.warn(
                    DEPENDENCY_MISMATCH,
                    f"Program {pid} is not in the template file; its name and contents cannot be checked",
                    f"zones[{z['zone']}].program_id",
                )
            else:
                dep.update(present_in_file=True, name=rec.name, sha256=sha256(rec.payload))
                if pid >= 1025:
                    report.warn(
                        DEPENDENCY_MISMATCH,
                        f"User Program {pid} '{rec.name}' is taken from the file; confirm the keyboard holds the same "
                        "Program (compare its sha256 after receiving it) before relying on this Multi",
                        f"zones[{z['zone']}].program_id",
                    )
            report.dependencies.append(dep)

    report.effective_zones = [zones[n] for n in range(1, 5)]
    return report
