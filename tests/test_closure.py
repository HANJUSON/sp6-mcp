import os
from pathlib import Path

import pytest

from sp6_mcp import multi_codec as mc
from sp6_mcp.closure import aux_records, export_fmlt, program_refs
from sp6_mcp.container import Container, encode_record
from sp6_mcp.errors import Sp6Error

from synth import alg_record, fx_record, program_payload, standard_bank


def test_program_refs_reads_fx_slots_and_layers():
    refs = program_refs(program_payload(fx=(5001, 0, 19), algs=(1, 6, 6, 120)), 7)
    assert refs.fx_ids == [5001, 19]
    assert refs.alg_ids == [1, 6, 6, 120]  # one per layer, not de-duplicated
    assert refs.notes == []


def test_program_refs_refuses_unknown_layout():
    with pytest.raises(Sp6Error) as e:
        program_refs(b"X" * 20 + b"PC3" + bytes(40), 7)
    assert e.value.code == "UNSUPPORTED_FORMAT"
    p = bytearray(program_payload(algs=(1, 1)))
    p[600:603] = b"\x03\x03\x51"  # stray marker off the layer grid
    with pytest.raises(Sp6Error):
        program_refs(bytes(p), 7)


def test_swapped_fx_slots_are_noted():
    p = bytearray(program_payload())
    p[117 + 2], p[129 + 2] = 0x53, 0x52
    assert program_refs(bytes(p), 7).notes


def test_aux_records():
    aux = aux_records(standard_bank())
    assert aux[("fx", 19)] == fx_record(19, "FX 19")
    assert aux[("alg", 6)] == alg_record(6)
    with pytest.raises(Sp6Error):
        aux_records(fx_record(19, "A") + fx_record(19, "B"))


def test_export_layout_and_user_program_exclusion():
    bank = Container(standard_bank())
    multi = encode_record("multi", 1055, bank.get("multi", 1).payload)
    ex = export_fmlt(bank, [87, 1200, 1, 1], multi)
    expected = (
        multi
        + bank.get("program", 87).encode() + fx_record(19, "FX 19") + alg_record(6) + alg_record(120)
        + bank.get("program", 1).encode()
        + fx_record(5001, "FX 5001") + fx_record(5002, "FX 5002") + fx_record(5003, "FX 5003")
        + alg_record(1) + alg_record(1) + alg_record(6) + alg_record(5)
    )
    assert ex.data == expected
    assert ex.program_ids == [87, 1] and ex.excluded_program_ids == [1200] and not ex.complete
    assert any("more than one zone" in n for n in ex.notes)
    Container(ex.data)  # still parses and round-trips


def test_missing_dependency_is_refused():
    bank = Container(standard_bank().replace(alg_record(120), b""))
    with pytest.raises(Sp6Error) as e:
        export_fmlt(bank, [87], encode_record("multi", 1055, bank.get("multi", 1).payload))
    assert e.value.code == "DEPENDENCY_MISMATCH"


PRIVATE = os.environ.get("SP6_MCP_PRIVATE_DIR")


@pytest.mark.skipif(not PRIVATE, reason="set SP6_MCP_PRIVATE_DIR to the folder holding the 만년설 files")
def test_reproduces_editor_export_byte_for_byte():
    root = Path(PRIVATE)
    reference = (root / "만년설_1050.fmlt").read_bytes()
    bank = Container.from_path(root / "SP6_user_multis_after_20261030.ksp6")
    multi = bank.get("multi", 1050)
    programs = [z.values["program_id"] for z in mc.decode(multi.payload).zones]
    ex = export_fmlt(bank, programs, multi.encode())
    assert ex.complete and ex.data == reference


def test_duplicate_fx_id_is_noted():
    assert any("same FX ID" in n for n in program_refs(program_payload(fx=(19, 19, 0)), 7).notes)


def test_fx_slot_coded_51_at_129_is_not_a_layer():
    p = bytearray(program_payload(fx=(5001, 5002, 5003), algs=(1, 6)))
    p[105 + 2], p[129 + 2] = 0x53, 0x51
    refs = program_refs(bytes(p), 7)
    assert refs.alg_ids == [1, 6] and refs.notes


def test_marker_like_bytes_after_last_layer_are_refused():
    p = bytearray(program_payload(algs=(1,)) + bytes(400))
    p[521 + 318 : 521 + 321] = b"\x03\x03\x52"
    with pytest.raises(Sp6Error) as e:
        program_refs(bytes(p), 7)
    assert e.value.code == "UNSUPPORTED_FORMAT"


def test_aux_backreference_and_regions():
    # A suffix followed by a different ID inside a record does not end it.
    tricky = b"FX***Data>0019FX*-*Data>x<FX***Data0020y<FX*-*Data<FX***Data0019"
    assert aux_records(tricky)[("fx", 19)] == tricky
    # In a bank, FX-looking bytes outside the FX table are ignored.
    bank = b"junk " + fx_record(7, "stray") + b" FX***Bank>" + fx_record(19, "real") + b"<FX***Bank"
    aux = aux_records(bank)
    assert ("fx", 7) not in aux and ("fx", 19) in aux


def test_repeated_program_noted_once():
    bank = Container(standard_bank())
    ex = export_fmlt(bank, [1, 1, 1, 1], encode_record("multi", 1055, bank.get("multi", 1).payload))
    assert sum("more than one zone" in n for n in ex.notes) == 1 and ex.complete


def test_missing_factory_program_is_reported():
    bank = Container(standard_bank())
    ex = export_fmlt(bank, [87, 300], encode_record("multi", 1055, bank.get("multi", 1).payload))
    assert ex.missing_program_ids == [300] and not ex.complete
