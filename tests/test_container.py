import pytest

from sp6_mcp.container import Container, encode_record
from sp6_mcp.errors import Sp6Error

from synth import multi_payload, program_payload, standard_bank


def test_roundtrip_is_byte_identical():
    data = standard_bank()
    c = Container(data)
    assert c.encode() == data
    assert [r.kind for r in c.records].count("multi") == 4


def test_inventory_and_listing():
    c = Container(standard_bank())
    inv = c.inventory()
    assert inv["multi"]["user_nonempty_ids"] == [1025]
    assert inv["program"]["user_nonempty_ids"] == [1200]
    assert inv["multi"]["user_range_complete_in_file"] is False
    rows = c.listing(kinds=["multi"], include_empty=True)
    assert [(r["id"], r["name"], r["empty_in_file"]) for r in rows] == [
        (1, "FACTORY ONE", False), (1025, "USER A", False), (1026, None, True), (1027, None, True)]


def test_replacement_only_changes_target():
    data = standard_bank()
    c = Container(data)
    new = multi_payload("NEW")
    out = c.encode({("multi", 1026): new})
    c2 = Container(out)
    assert c2.get("multi", 1026).payload == new
    for r in c.records:
        if (r.kind, r.object_id) != ("multi", 1026):
            assert c2.get(r.kind, r.object_id).payload == r.payload
    # Everything before the replaced record is unchanged.
    assert out[: c.get("multi", 1026).start] == data[: c.get("multi", 1026).start]


def test_replacement_of_missing_record_fails():
    with pytest.raises(Sp6Error) as e:
        Container(standard_bank()).encode({("multi", 1500): b"x"})
    assert e.value.code == "UNSUPPORTED_FORMAT"


def test_duplicate_records_rejected():
    rec = encode_record("multi", 1025, multi_payload())
    with pytest.raises(Sp6Error):
        Container(rec + rec)


def test_no_records_rejected():
    with pytest.raises(Sp6Error):
        Container(b"just some bytes")


def test_bad_object_id():
    with pytest.raises(Sp6Error):
        encode_record("multi", 10000, b"")


def test_corrupted_suffix_cannot_swallow_records():
    a = b"Prog*Dat*>0001Prog_Dat*>AAAA<Prog_Dat*<Prog*Dat*0009"  # bad suffix ID
    b = encode_record("program", 2, b"BBBB")
    c = encode_record("program", 1, b"CCCC")
    with pytest.raises(Sp6Error):
        Container(a + b + c)


def test_payload_with_envelope_rejected_on_encode():
    with pytest.raises(Sp6Error):
        encode_record("multi", 1, b"x<Set*-Dat*<Set**Dat*y")
