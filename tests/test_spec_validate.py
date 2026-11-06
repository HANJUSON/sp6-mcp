import copy
import hashlib

import pytest

from sp6_mcp.errors import Sp6Error
from sp6_mcp.spec import parse_spec, spec_json_schema
from sp6_mcp.validate import load_template, note_name, validate

from synth import standard_bank


@pytest.fixture
def template_file(tmp_path):
    p = tmp_path / "bank.ksp6"
    p.write_bytes(standard_bank())
    return p


def sheet(template_file, **over):
    base = {
        "schema_version": "1", "operation": "create_multi", "name": "STANDUP_LIVE",
        "preferred_user_multi_id": 1055,
        "template": {"source_path": str(template_file), "sha256": hashlib.sha256(template_file.read_bytes()).hexdigest(),
                     "kind": "multi", "id": 1},
        "range_policy": "split",
        "zones": [
            {"zone": 1, "enabled": True, "program_id": 87, "midi_channel": 1, "key_low": 21, "key_high": 83, "transpose_semitones": 0},
            {"zone": 2, "enabled": True, "program_id": 1200, "midi_channel": 2, "key_low": 84, "key_high": 108, "transpose_semitones": -24},
            {"zone": 3, "enabled": False}, {"zone": 4, "enabled": False},
        ],
    }
    base.update(over)
    return base


def run(data, template_file):
    spec = parse_spec(data)
    return validate(spec, load_template(spec, template_file))


def test_standup_sheet_is_buildable(template_file):
    r = run(sheet(template_file), template_file)
    assert r.valid and r.can_build, r.to_dict()
    z2 = r.effective_zones[1]
    assert z2["sounding_range"]["note_names_out"] == "C4-C6"
    assert z2["sounding_range"]["physical_keys"] == "C6-C8"
    codes = {w.code for w in r.issues}
    assert "SLOT_STATE_UNKNOWN" in codes  # file-empty is never proof
    deps = {d["program_id"]: d for d in r.dependencies}
    assert deps[1200]["present_in_file"] and deps[1200]["name"] == "Lead"


def test_unsupported_fields_block_build(template_file):
    d = sheet(template_file)
    d["zones"][1].update(volume=105, arp_button="yes", controllers={"modwheel": {"enabled": True}})
    r = run(d, template_file)
    assert not r.can_build
    assert set(r.unsupported_fields) == {"zones[2].volume", "zones[2].arp_button", "zones[2].controllers"}


def test_overlap_rejected_in_split_and_allowed_in_layer(template_file):
    d = sheet(template_file)
    d["zones"][0]["key_high"] = 90
    assert any("overlap" in e.message for e in run(d, template_file).errors)
    d["range_policy"] = "layer"
    assert run(d, template_file).valid


def test_inverted_range_needs_confirmation(template_file):
    d = sheet(template_file)
    d["zones"][0].update(key_low=83, key_high=21)
    assert not run(d, template_file).valid
    d["zones"][0]["allow_inverted_key_range"] = True
    r = run(d, template_file)
    # 83/21 sounds on 0-21 and 83-127, which still overlaps zone 2 (84-108).
    assert any("overlap" in e.message for e in r.errors)
    d["zones"][1]["enabled"] = False
    assert run(d, template_file).valid


def test_same_channel_different_program(template_file):
    d = sheet(template_file)
    d["zones"][1]["midi_channel"] = 1
    assert any("channel 1" in e.message for e in run(d, template_file).errors)


def test_occupied_target_in_file(template_file):
    d = sheet(template_file, preferred_user_multi_id=1025)
    assert any(e.code == "SLOT_OCCUPIED" for e in run(d, template_file).errors)


def test_hash_mismatch(template_file):
    d = sheet(template_file)
    d["template"]["sha256"] = "0" * 64
    with pytest.raises(Sp6Error) as e:
        run(d, template_file)
    assert e.value.code == "SOURCE_CHANGED"


def test_missing_dependency_warns(template_file):
    d = sheet(template_file)
    d["zones"][0]["program_id"] = 300
    r = run(d, template_file)
    assert any(w.code == "DEPENDENCY_MISMATCH" for w in r.issues)


@pytest.mark.parametrize("mutate,field", [
    (lambda d: d.update(name="X" * 17), "name"),
    (lambda d: d.update(name="Café"), "name"),
    (lambda d: d["zones"][0].update(key_low=200), "zones[0].key_low"),
    (lambda d: d["zones"][0].update(bogus=1), "zones[0].bogus"),
    (lambda d: d["zones"][0].update(volume=None), "zones[0].volume"),
    (lambda d: d["zones"][0].update(midi_channel="1"), "zones[0].midi_channel"),
    (lambda d: d.update(preferred_user_multi_id=100), "preferred_user_multi_id"),
    (lambda d: d["zones"].append({"zone": 1}), "zones"),
])
def test_structural_errors_have_paths(template_file, mutate, field):
    d = copy.deepcopy(sheet(template_file))
    mutate(d)
    with pytest.raises(Sp6Error) as e:
        parse_spec(d)
    assert e.value.code == "INVALID_SPEC"
    assert e.value.field.startswith(field)


def test_none_level_is_accepted_structurally(template_file):
    d = sheet(template_file)
    d["zones"][0]["volume"] = "none"
    assert parse_spec(d).zones[0].volume == "none"


def test_note_names():
    assert [note_name(n) for n in (21, 60, 83, 84, 108)] == ["A0", "C4", "B4", "C5", "C8"][:2] + ["B5", "C6", "C8"]


def test_schema_exports():
    s = spec_json_schema()
    assert s["additionalProperties"] is False


def test_example_sheet_parses():
    import json
    from pathlib import Path
    data = json.loads((Path(__file__).parents[1] / "examples" / "standup.sheet.example.json").read_text(encoding="utf-8"))
    spec = parse_spec(data)
    assert spec.zone(2).transpose_semitones == -24


def test_transpose_envelope_agrees_with_codec(template_file):
    d = sheet(template_file)
    d["zones"][1]["transpose_semitones"] = 25
    with pytest.raises(Sp6Error):
        parse_spec(d)


def test_blank_name_rejected(template_file):
    with pytest.raises(Sp6Error):
        parse_spec(sheet(template_file, name="   "))


def test_omitted_enabled_zone_warns(template_file):
    d = sheet(template_file)
    d["zones"] = d["zones"][:2]
    r = run(d, template_file)
    assert any(w.field == "zones[3]" and w.severity == "warning" for w in r.issues)
