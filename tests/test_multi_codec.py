import pytest

from sp6_mcp import multi_codec as mc
from sp6_mcp.errors import Sp6Error

from synth import multi_payload


def test_decode_synthetic_fields():
    p = multi_payload("STANDUP_LIVE", [
        dict(program=87, low=21, high=83),
        dict(program=1200, low=84, high=108, transpose=-24, volume=105),
        dict(enabled=False), dict(enabled=False, program=1022)])
    v = mc.decode(p)
    assert v.name == "STANDUP_LIVE"
    z1, z2, z3, z4 = (z.values for z in v.zones)
    assert (z1["program_id"], z1["key_low"], z1["key_high"]) == (87, 21, 83)
    assert (z2["program_id"], z2["transpose_semitones"], z2["volume"]) == (1200, -24, 105)
    assert z3["enabled"] is False and z4["program_id"] == 1022
    assert [z["midi_channel"] for z in (z1, z2, z3, z4)] == [1, 2, 3, 4]


def test_program_id_7bit_encoding():
    assert mc.encode_program_id(1200) == (9, 48)
    assert mc.decode_program_id(9, 48) == 1200
    assert mc.encode_program_id(67) == (0, 67)
    for pid in (1, 127, 128, 1024, 1025, 2048):
        assert mc.decode_program_id(*mc.encode_program_id(pid)) == pid


def test_apply_changes_only_allowed_offsets():
    p = multi_payload()
    out = mc.apply(p, "NEW NAME", {
        1: {"program_id": 87, "key_low": 21, "key_high": 83},
        2: {"program_id": 1200, "key_low": 84, "key_high": 108, "transpose_semitones": -24},
        3: {"enabled": False}, 4: {"enabled": False, "midi_channel": 16}})
    assert len(out) == len(p)
    assert set(mc.changed_offsets(p, out)) <= mc.allowed_offsets(p)
    v = mc.decode(out)
    assert v.name == "NEW NAME"
    assert v.zones[1].values["transpose_semitones"] == -24
    assert v.zones[3].values["midi_channel"] == 16


def test_noop_apply_is_identity():
    p = multi_payload()
    assert mc.apply(p, None, {}) == p


@pytest.mark.parametrize("field,value", [("volume", 90), ("pan", 10), ("arp_button", "yes"), ("velocity_low", 1)])
def test_unverified_fields_rejected(field, value):
    with pytest.raises(Sp6Error) as e:
        mc.apply(multi_payload(), None, {1: {field: value}})
    assert e.value.code == "UNSUPPORTED_FIELD"
    assert e.value.field == f"zones[1].{field}"


@pytest.mark.parametrize("field,value", [
    ("program_id", 0), ("program_id", 2049), ("key_low", 128), ("midi_channel", 0),
    ("midi_channel", 17), ("transpose_semitones", 48), ("enabled", 1), ("key_high", True)])
def test_invalid_values_rejected(field, value):
    with pytest.raises(Sp6Error) as e:
        mc.apply(multi_payload(), None, {1: {field: value}})
    assert e.value.code == "INVALID_SPEC"


@pytest.mark.parametrize("name", ["", "x" * 17, "Café", "tab\there"])
def test_bad_names(name):
    with pytest.raises(Sp6Error):
        mc.apply(multi_payload(), name, {})


def test_wrong_zone_count_is_unsupported():
    p = multi_payload()
    first = p.index(b"\x03\x1b")
    broken = p[:first] + b"\x00\x00" + p[first + 2:]
    with pytest.raises(Sp6Error) as e:
        mc.decode(broken)
    assert e.value.code == "UNSUPPORTED_FORMAT"


def test_missing_tag_is_unsupported():
    p = bytearray(multi_payload())
    p[20:23] = b"XYZ"
    with pytest.raises(Sp6Error):
        mc.decode(bytes(p))


def test_field_registry_documents_evidence():
    for f in mc.FIELDS.values():
        assert f.evidence
    assert "volume" not in mc.WRITABLE_FIELDS
