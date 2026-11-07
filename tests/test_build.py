import hashlib
import json

import pytest

from sp6_mcp import multi_codec as mc
from sp6_mcp.build import build, diff_files
from sp6_mcp.container import Container
from sp6_mcp.errors import Sp6Error
from sp6_mcp.spec import parse_spec
from sp6_mcp.workspace import Workspace

from synth import standard_bank


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "root"
    (root / "private").mkdir(parents=True)
    (root / "private" / "bank.ksp6").write_bytes(standard_bank())
    return Workspace(root, root / "artifacts")


def sheet(ws, **over):
    data = (ws.work_root / "private" / "bank.ksp6").read_bytes()
    d = {
        "schema_version": "1", "operation": "create_multi", "name": "STANDUP_LIVE",
        "preferred_user_multi_id": 1026,
        "template": {"source_path": "private/bank.ksp6", "sha256": hashlib.sha256(data).hexdigest(), "kind": "multi", "id": 1},
        "zones": [
            {"zone": 1, "enabled": True, "program_id": 87, "midi_channel": 1, "key_low": 21, "key_high": 83},
            {"zone": 2, "enabled": True, "program_id": 1200, "midi_channel": 2, "key_low": 84, "key_high": 108, "transpose_semitones": -24},
            {"zone": 3, "enabled": False}, {"zone": 4, "enabled": False}],
    }
    d.update(over)
    return parse_spec(d)


def test_build_fmlt(ws):
    before = (ws.work_root / "private" / "bank.ksp6").read_bytes()
    r = build(sheet(ws), ws, "standup.fmlt", "fmlt")
    assert (ws.work_root / "private" / "bank.ksp6").read_bytes() == before  # source untouched
    c = Container(r.output_path.read_bytes())
    assert [(x.kind, x.object_id) for x in c.records][0] == ("multi", 1026)
    assert r.report["included_program_ids"] == [87, 1]  # user Program 1200 deliberately left out
    deps = r.report["fmlt_dependencies"]
    assert deps["fx_ids"] == {"87": [19], "1": [5001, 5002, 5003]} and deps["alg_ids"]["87"] == [6, 120]
    v = mc.decode(c.get("multi", 1026).payload)
    assert v.name == "STANDUP_LIVE"
    assert [z.values["enabled"] for z in v.zones] == [True, True, False, False]
    assert v.zones[1].values["transpose_semitones"] == -24
    m = json.loads(r.manifest_path.read_text(encoding="utf-8"))
    assert m["result"]["phase"] == "file_built" and m["result"]["device_write_state"] == "not_sent"
    assert r.report["changes"]["uninterpreted_bytes_changed"] == 0


def test_build_ksp6_preserves_everything_else(ws):
    r = build(sheet(ws), ws, "out/standup.ksp6", "ksp6")
    d = diff_files(ws.work_root / "private" / "bank.ksp6", r.output_path)
    assert d["records_changed"] == ["multi:1026"] and not d["records_added"] and d["records_removed_count"] == 0
    assert d["multi_field_diffs"][0]["note"] == "empty payload on one side"
    assert r.report["preserved"]["non_record_bytes_identical"]


def test_build_is_deterministic(ws):
    a = build(sheet(ws), ws, "a.fmlt", "fmlt").output_path.read_bytes()
    b = build(sheet(ws), ws, "b.fmlt", "fmlt").output_path.read_bytes()
    assert build(sheet(ws), ws, "a.ksp6").output_path.read_bytes() == build(sheet(ws), ws, "b.ksp6").output_path.read_bytes()
    assert a == b


def test_diff_with_source_multi(ws):
    r = build(sheet(ws), ws, "x.fmlt", "fmlt")
    d = diff_files(ws.work_root / "private" / "bank.ksp6", r.output_path, source_multi_id=1)
    z = {e["zone"]: e["fields"] for e in d["multi_field_diffs"][0]["zones"]}
    assert z[2]["transpose_semitones"] == {"from": 0, "to": -24}


@pytest.mark.parametrize("path", ["../escape.ksp6", "C:/abs.ksp6", "/abs.ksp6", "C:rel.ksp6", "a/../../x.ksp6"])
def test_output_path_denied(ws, path):
    with pytest.raises(Sp6Error) as e:
        build(sheet(ws), ws, path)
    assert e.value.code == "OUTPUT_PATH_DENIED"


def test_existing_output_denied(ws):
    build(sheet(ws), ws, "same.ksp6")
    with pytest.raises(Sp6Error) as e:
        build(sheet(ws), ws, "same.ksp6")
    assert e.value.code == "OUTPUT_PATH_DENIED"


def test_occupied_target_refused(ws):
    with pytest.raises(Sp6Error) as e:
        build(sheet(ws, preferred_user_multi_id=1025), ws, "o.ksp6")
    assert e.value.code == "SLOT_OCCUPIED"


def test_unsupported_field_refused(ws):
    s = sheet(ws)
    d = s.model_dump(mode="json", exclude_unset=True)
    d["zones"][1]["volume"] = 105
    with pytest.raises(Sp6Error) as e:
        build(parse_spec(d), ws, "u.ksp6")
    assert e.value.code == "UNSUPPORTED_FIELD"
    assert not (ws.artifacts_dir / "u.ksp6").exists()


def test_ksp6_needs_existing_empty_record(ws):
    with pytest.raises(Sp6Error):
        build(sheet(ws, preferred_user_multi_id=1500), ws, "k.ksp6", "ksp6")


def test_extension_must_match_format(ws):
    with pytest.raises(Sp6Error) as e:
        build(sheet(ws), ws, "wrong.fmlt")  # default format is ksp6
    assert e.value.field == "output_path"


def test_fmlt_flags_left_out_user_programs(ws):
    r = build(sheet(ws), ws, "p.fmlt", "fmlt")
    assert any(w["code"] == "PARTIAL_EXPORT" and "1200" in w["message"] for w in r.report["warnings"])
    assert r.report["fmlt_dependencies"]["complete"] is False


def test_fmlt_complete_with_factory_programs_only(ws):
    d = sheet(ws).model_dump(mode="json", exclude_unset=True)
    d["zones"][1]["program_id"] = 1
    d["zones"][1]["midi_channel"] = 2
    r = build(sheet(ws, zones=d["zones"]), ws, "f.fmlt", "fmlt")
    assert r.report["fmlt_dependencies"]["complete"] is True
    assert not any(w["code"] == "PARTIAL_EXPORT" for w in r.report["warnings"])


@pytest.mark.parametrize("path", ["prev.ksp6:evil.ksp6", "x.ksp6.", "dir. /x.ksp6", "sub/aux.ksp6", "CON.ksp6", "a?.ksp6"])
def test_unsafe_windows_names_denied(ws, path):
    with pytest.raises(Sp6Error) as e:
        build(sheet(ws), ws, path)
    assert e.value.code in ("OUTPUT_PATH_DENIED", "INVALID_SPEC")  # "x.ksp6." fails the extension rule first


def test_write_new_file_never_deletes_foreign_files(tmp_path):
    from sp6_mcp.workspace import write_new_file
    foreign = tmp_path / "out.ksp6.partial"
    foreign.write_bytes(b"USER DATA")
    existing = tmp_path / "taken.ksp6"
    existing.write_bytes(b"KEEP")
    write_new_file(tmp_path / "out.ksp6", b"new")
    assert foreign.read_bytes() == b"USER DATA"
    with pytest.raises(Sp6Error):
        write_new_file(existing, b"overwrite?")
    assert existing.read_bytes() == b"KEEP"
    assert not list(tmp_path.glob(".sp6tmp-*"))


def test_fmlt_verifies_fx_alg_bytes_and_reports_missing(ws):
    r = build(sheet(ws), ws, "v.fmlt", "fmlt")
    assert r.report["preserved"]["fx_alg_records_copied_verbatim"] is True
    d = sheet(ws).model_dump(mode="json", exclude_unset=True)
    d["zones"][2] = {"zone": 3, "enabled": False, "program_id": 300}
    r = build(sheet(ws, zones=d["zones"]), ws, "m.fmlt", "fmlt")
    assert 300 in r.report["fmlt_dependencies"]["missing_program_ids"]
    assert any(w["code"] == "PARTIAL_EXPORT" and "300" in w["message"] for w in r.report["warnings"])


def test_fmlt_unknown_program_layout_aborts(ws):
    from synth import bank, multi_payload, program_payload
    data = bank({1: multi_payload("T", [{"program": 9}, {}, {}, {}]), 1026: b""},
                {1: program_payload(), 9: b"Odd" + bytes(17) + b"PC3" + bytes(30)})
    (ws.work_root / "private" / "bank.ksp6").write_bytes(data)
    d = sheet(ws).model_dump(mode="json", exclude_unset=True)
    d["zones"] = [{"zone": 1, "enabled": True}, {"zone": 2, "enabled": False}, {"zone": 3, "enabled": False}, {"zone": 4, "enabled": False}]
    with pytest.raises(Sp6Error) as e:
        build(sheet(ws, zones=d["zones"]), ws, "x.fmlt", "fmlt")
    assert e.value.code == "UNSUPPORTED_FORMAT"
    assert not (ws.artifacts_dir / "x.fmlt").exists()
