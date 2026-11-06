import hashlib
import json

from sp6_mcp.cli import main

from synth import standard_bank


def test_cli_flow(tmp_path, monkeypatch, capsys):
    (tmp_path / "bank.ksp6").write_bytes(standard_bank())
    monkeypatch.setenv("SP6_MCP_WORK_ROOT", str(tmp_path))
    monkeypatch.delenv("SP6_MCP_ARTIFACTS_DIR", raising=False)
    h = hashlib.sha256(standard_bank()).hexdigest()
    sheet = {"schema_version": "1", "operation": "create_multi", "name": "CLI", "preferred_user_multi_id": 1027,
             "template": {"source_path": "bank.ksp6", "sha256": h, "kind": "multi", "id": 1},
             "zones": [{"zone": 1, "key_high": 59}, {"zone": 2, "key_low": 60, "midi_channel": 2}, {"zone": 3, "enabled": False}, {"zone": 4, "enabled": False}]}
    (tmp_path / "sheet.json").write_text(json.dumps(sheet), encoding="utf-8")
    assert main(["inspect", "bank.ksp6", "--kind", "multi", "--user"]) == 0
    assert json.loads(capsys.readouterr().out)["objects"][0]["id"] == 1025
    assert main(["validate", str(tmp_path / "sheet.json")]) == 0
    capsys.readouterr()
    assert main(["build", str(tmp_path / "sheet.json"), "cli.ksp6"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["target_multi_id"] == 1027
    assert (tmp_path / "artifacts" / "cli.ksp6").is_file()
    assert main(["read", "bank.ksp6", "9"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "INVALID_SPEC"
