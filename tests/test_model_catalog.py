"""Runtime model discovery: real stdio exchanges with fake CLIs, never inference."""

import json
import os
import sys
from types import SimpleNamespace

import pytest


def test_catalog_uses_live_ids_and_capabilities_without_inventing_levels(t_mod):
    codex = t_mod._config_catalog("codex", [
        {"model": "new-model", "supportedReasoningEfforts": [{"reasoningEffort": "ultra"},
                                                           {"reasoningEffort": "low"}]},
        {"model": "hidden", "hidden": True},
        {"model": "new-model"}, None, {}, {"model": "bad model"},
        {"model": "plain", "supportedReasoningEfforts": "invalid"},
    ])
    assert [m["id"] for m in codex] == ["new-model", "plain"]
    assert codex[0]["efforts"] == ["ultra", "low"] and codex[1]["efforts"] == []
    claude = t_mod._config_catalog("claude", [
        {"value": "opus", "resolvedModel": "claude-new-opus", "supportsEffort": True,
         "supportedEffortLevels": ["high", "max", "high", None, "bad value"]},
        {"value": "haiku", "resolvedModel": "claude-new-haiku"},
        {"value": "disabled", "supportsEffort": False, "supportedEffortLevels": ["high"]},
        {"value": "custom", "resolvedModel": None},
    ])
    assert claude[0]["label"] == "claude-new-opus (opus)"
    assert claude[0]["efforts"] == ["high", "max"]
    assert claude[1]["efforts"] == claude[2]["efforts"] == []
    assert claude[3]["resolved"] == "custom"
    assert t_mod._config_catalog("claude", {}) == []


def test_picker_rows_preserve_saved_models_but_label_unknown_capabilities(t_mod):
    catalog = t_mod._config_catalog("claude", [{"value": "opus", "resolvedModel": "claude-new-opus",
                    "supportsEffort": True, "supportedEffortLevels": ["high", "max"]}])
    rows = dict(t_mod._config_model_rows("retired", catalog))
    assert "not in tool's current list" in rows["retired"] and "__refresh__" in rows
    assert "(saved)" in dict(t_mod._config_model_rows("claude-new-opus", catalog))["claude-new-opus"]
    assert len(t_mod._config_model_rows("opus", catalog)) == 4
    rows = dict(t_mod._config_effort_rows("ultra", "opus", catalog))
    assert set(rows) == {"", "high", "max", "ultra", "__refresh__"}
    assert "not verified" in rows["ultra"]
    assert [v for v, _ in t_mod._config_effort_rows("", "", catalog)] == ["", "__refresh__"]


def test_switch_model_retains_only_reported_effort(t_mod):
    catalog = t_mod._config_catalog("codex", [{"model": "a", "supportedReasoningEfforts": [
        {"reasoningEffort": "high"}]}])
    cfg = SimpleNamespace(models={}, efforts={"codex": "high"}, fast={})
    assert not t_mod._config_set_model(cfg, "codex", "a", catalog)
    assert cfg.efforts["codex"] == "high"
    assert t_mod._config_set_model(cfg, "codex", "custom-model", catalog)
    assert cfg.efforts["codex"] == ""
    cfg.efforts["codex"] = "high"
    assert t_mod._config_set_model(cfg, "codex", "", catalog)


def test_effort_config_bridge_and_validation(t_mod, tmp_path, monkeypatch):
    path = tmp_path / "config.sh"
    path.write_text("DEV_EFFORT[claude]=max\nDEV_EFFORT[codex]=ultra\n")
    monkeypatch.setattr(t_mod, "CONFIG", str(path))
    assert t_mod.Config().efforts == {"claude": "max", "codex": "ultra"}
    assert "DEV_EFFORT[codex]=ultra" in t_mod._config_text("", {"DEV_EFFORT[codex]": "ultra"})
    with pytest.raises(ValueError, match="effort"):
        t_mod._config_assignment("DEV_EFFORT[codex]", "high; exit")


FAKE_CLI = r'''
import json, os, sys
with open(os.environ['CATALOG_LOG'], 'a') as f:
    f.write(json.dumps({'argv': sys.argv, 'nested': 'CLAUDECODE' in os.environ}) + '\n')
for line in sys.stdin:
    request = json.loads(line)
    with open(os.environ['CATALOG_LOG'], 'a') as f:
        f.write(json.dumps(request) + '\n')
    method = request.get('method')
    if method == 'initialized':
        continue
    if method == 'initialize':
        result = {}
    elif method == 'model/list':
        if os.environ.get('CATALOG_FAIL'):
            print(json.dumps({'id': request['id'], 'error': {'message': 'offline'}}), flush=True)
            continue
        cursor = request['params'].get('cursor')
        result = {'data': [{'model': 'second' if cursor else 'first'}],
                  'nextCursor': None if cursor else 'page-2'}
    elif request.get('type') == 'control_request':
        print(json.dumps({'type': 'control_response', 'response': {
            'request_id': request['request_id'], 'subtype': 'success',
            'response': {'models': [{'value': 'opus', 'resolvedModel': 'claude-new-opus'}]}}}), flush=True)
        continue
    else:
        raise AssertionError('Unexpected request (no prompts or threads allowed): ' + line)
    # Exercise unrelated notifications and multiple buffered NDJSON lines.
    print('not-json\n{}\n' + json.dumps({'id': request['id'], 'result': result}), flush=True)
'''


@pytest.fixture
def fake_clis(t_mod, tmp_path, monkeypatch):
    for agent in ("claude", "codex"):
        path = tmp_path / agent
        path.write_text(f"#!{sys.executable}\n" + FAKE_CLI)
        path.chmod(0o755)
    log = tmp_path / "requests.jsonl"
    monkeypatch.setattr(t_mod, "HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("CATALOG_LOG", str(log))
    monkeypatch.setenv("CLAUDECODE", "1")
    return log


def test_codex_discovery_initializes_and_reads_every_page(t_mod, fake_clis):
    assert t_mod._config_live_models("codex") == [{"model": "first"}, {"model": "second"}]
    events = [json.loads(line) for line in fake_clis.read_text().splitlines()]
    assert events[0]["argv"][1:] == ["app-server"]
    assert [event["method"] for event in events[1:]] == ["initialize", "initialized", "model/list", "model/list"]
    assert events[-1]["params"]["cursor"] == "page-2"
    assert events[-1]["params"]["includeHidden"] is False


def test_claude_discovery_only_initializes_without_hooks_or_persistence(t_mod, fake_clis):
    assert t_mod._config_live_models("claude") == [{"value": "opus", "resolvedModel": "claude-new-opus"}]
    events = [json.loads(line) for line in fake_clis.read_text().splitlines()]
    argv = events[0]["argv"]
    assert "--no-session-persistence" in argv and "--strict-mcp-config" in argv
    assert json.loads(argv[argv.index("--settings") + 1])["disableAllHooks"] is True
    assert argv[argv.index("--setting-sources") + 1] == "user" and not events[0]["nested"]
    assert len(events) == 2 and events[1]["request"] == {"subtype": "initialize"}


def test_discovery_errors_are_bounded_and_do_not_leak_vendor_response(t_mod, fake_clis, monkeypatch):
    monkeypatch.setenv("CATALOG_FAIL", "1")
    with pytest.raises(ValueError, match="model metadata"):
        t_mod._config_live_models("codex")


def test_metadata_timeout_and_eof_reap_the_process(t_mod):
    with pytest.raises(ValueError, match="timed out"):
        with t_mod._ConfigRPC([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.1) as rpc:
            rpc.receive("missing")
    assert rpc.process.poll() is not None
    with pytest.raises(ValueError, match="exited"):
        with t_mod._ConfigRPC([sys.executable, "-c", "pass"]) as rpc:
            rpc.receive("missing")
    assert rpc.process.poll() is not None


def test_fast_support_comes_from_each_vendors_capabilities(t_mod):
    catalog = t_mod._config_catalog('codex', [
        {'model': 'a', 'serviceTiers': [{'id': 'priority'}]},
        {'model': 'b', 'serviceTiers': [{'id': 'fast'}]},
        {'model': 'c', 'serviceTiers': [], 'additionalSpeedTiers': ['fast']},
        {'model': 'd', 'additionalSpeedTiers': ['fast']},
        {'model': 'e', 'additionalSpeedTiers': 1},
    ])
    assert [m['fast'] for m in catalog] == [True, True, False, True, False]
    claude = t_mod._config_catalog('claude', [{'value': 'opus', 'supportsFastMode': True}, {'value': 'haiku'}])
    assert [m['fast'] for m in claude] == [True, False]
    assert t_mod._config_assignment('DEV_FAST[codex]', '1') == 'DEV_FAST[codex]=1'
    with pytest.raises(ValueError, match='fast mode'):
        t_mod._config_assignment('DEV_FAST[codex]', 'yes')
