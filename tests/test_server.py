import asyncio
import hashlib
import json
import sys

import pytest
from mcp import Client, StdioServerParameters

from sp6_mcp.server import create_server
from sp6_mcp.workspace import Workspace

from synth import standard_bank


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "bank.ksp6").write_bytes(standard_bank())
    return Workspace(root, root / "artifacts")


def sheet(ws):
    h = hashlib.sha256((ws.work_root / "bank.ksp6").read_bytes()).hexdigest()
    return {
        "schema_version": "1", "operation": "create_multi", "name": "STANDUP_LIVE",
        "preferred_user_multi_id": 1026,
        "template": {"source_path": "bank.ksp6", "sha256": h, "kind": "multi", "id": 1},
        "zones": [
            {"zone": 1, "enabled": True, "program_id": 87, "key_low": 21, "key_high": 83},
            {"zone": 2, "enabled": True, "program_id": 1200, "key_low": 84, "key_high": 108, "transpose_semitones": -24},
            {"zone": 3, "enabled": False}, {"zone": 4, "enabled": False}],
    }


def call(ws, name, args=None):
    async def go():
        async with Client(create_server(ws)) as client:
            return await client.call_tool(name, args or {})
    return asyncio.run(go())


def payload(result):
    assert not result.is_error, result.content
    return result.structured_content["result"] if "result" in (result.structured_content or {}) else json.loads(result.content[0].text)["result"]


def test_tool_list_matches_capabilities(ws):
    async def go():
        async with Client(create_server(ws)) as client:
            return await client.list_tools()
    tools = asyncio.run(go()).tools
    names = sorted(t.name for t in tools)
    caps = payload(call(ws, "sp6_capabilities"))
    assert names == sorted(caps["tools"])
    assert not any("write" in n or "device" in n for n in names)
    build_tool = next(t for t in tools if t.name == "sp6_multi_build")
    assert build_tool.annotations.read_only_hint is False and build_tool.annotations.destructive_hint is False


def test_inspect_validate_build_diff(ws):
    inv = payload(call(ws, "sp6_file_inspect", {"path": "bank.ksp6", "kind": "multi", "include_empty": True}))
    assert inv["total"] == 4
    rd = payload(call(ws, "sp6_multi_read", {"path": "bank.ksp6", "multi_id": 1}))
    assert rd["zones"][0]["program_name"] == "Piano"
    v = payload(call(ws, "sp6_multi_validate", {"spec": sheet(ws)}))
    assert v["can_build"]
    b = payload(call(ws, "sp6_multi_build", {"spec": sheet(ws), "output_path": "standup.fmlt"}))
    assert b["phase"] == "file_built" and b["device_write_state"] == "not_sent"
    d = payload(call(ws, "sp6_artifact_diff", {"source_path": "bank.ksp6", "artifact_path": "standup.fmlt", "source_multi_id": 1}))
    assert d["multi_field_diffs"]


def test_errors_are_structured(ws):
    bad = sheet(ws)
    bad["zones"][0]["key_low"] = 300
    r = call(ws, "sp6_multi_validate", {"spec": bad})
    assert r.is_error
    err = json.loads(r.content[0].text.split(": ", 1)[-1]) if not r.content[0].text.startswith("{") else json.loads(r.content[0].text)
    assert err["error"]["code"] == "INVALID_SPEC" and err["error"]["field"] == "zones[0].key_low"
    assert err["error"]["device_write_state"] == "none"


def test_build_outside_artifacts_denied(ws):
    r = call(ws, "sp6_multi_build", {"spec": sheet(ws), "output_path": "../x.fmlt"})
    assert r.is_error and "OUTPUT_PATH_DENIED" in r.content[0].text


def test_stdio_server_starts_and_lists_tools(ws, monkeypatch):
    params = StdioServerParameters(command=sys.executable, args=["-m", "sp6_mcp.server"],
                                   env={"SP6_MCP_WORK_ROOT": str(ws.work_root), "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")})
    async def go():
        async with Client(params) as client:
            return await client.list_tools()
    tools = asyncio.run(go()).tools
    assert "sp6_multi_build" in {t.name for t in tools}


def test_object_find(ws):
    r = payload(call(ws, "sp6_object_find", {"path": "bank.ksp6", "query": "pa"}))
    assert [(m["id"], m["name"], m["user"]) for m in r["matches"]] == [(87, "Pad", False)]
    r = payload(call(ws, "sp6_object_find", {"path": "bank.ksp6", "query": "user", "kind": "multi"}))
    assert r["matches"][0]["id"] == 1025 and r["matches"][0]["user"]
