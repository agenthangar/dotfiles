"""Session defaults: safe persistence, picker choices, and save/cancel behavior."""

import io
import stat
from types import SimpleNamespace

import pytest


def defaults(agent, models):
    return {"DEV_AGENT_DEFAULT": agent, **{f"DEV_MODEL[{a}]": models.get(a, "") for a in ("claude", "codex")}}


def test_config_bridge_reads_model_defaults(t_mod, tmp_path, monkeypatch):
    path = tmp_path / "config.sh"
    path.write_text("DEV_AGENT_DEFAULT=codex\nDEV_AGENT[api]=claude\n"
                    "DEV_MODEL[claude]=sonnet\\[1m\\]\nDEV_MODEL[codex]=local/model\n")
    monkeypatch.setattr(t_mod, "CONFIG", str(path))
    cfg = t_mod.Config()
    assert cfg.models == {"claude": "sonnet[1m]", "codex": "local/model"}
    assert cfg.agent_for("web") == "codex"
    assert cfg.agent_for("api") == "claude"


def test_config_managed_block_preserves_shell_and_is_idempotent(t_mod):
    original = '# my config\nDEV_AGENT_DEFAULT=claude\nsource "$HOME/private.zsh"'
    models = {"claude": "sonnet[1m]", "codex": "local/model"}
    updated = t_mod._config_text(original, defaults("codex", models))
    assert updated.startswith(original + "\n\n")
    assert "DEV_MODEL[claude]='sonnet[1m]'" in updated
    assert t_mod._config_text(updated, defaults("codex", models)) == updated
    # Hand-written content appended later is preserved; our latest choice goes last.
    updated += "DEV_AGENT_DEFAULT=claude\n"
    cleared = t_mod._config_text(updated, defaults("claude", {}))
    assert cleared.count(t_mod._CONFIG_BEGIN) == 1
    assert cleared.count("DEV_AGENT_DEFAULT=claude") == 3
    assert "DEV_MODEL[claude]=''\nDEV_MODEL[codex]=''" in cleared
    assert "local/model" not in cleared


@pytest.mark.parametrize("text", [
    "# >>> t config defaults >>>\n", "# <<< t config defaults <<<\n",
    "# <<< t config defaults <<<\n# >>> t config defaults >>>\n",
    "# >>> t config defaults >>>\n# >>> t config defaults >>>\n# <<< t config defaults <<<\n",
])
def test_config_refuses_broken_markers(t_mod, text):
    with pytest.raises(ValueError, match="malformed"):
        t_mod._config_text(text, defaults("claude", {}))


@pytest.mark.parametrize("model", ["a b", "$(touch /tmp/no)", "x\ny", "--help", "a;exit", "\x1b[31m"])
def test_config_rejects_non_model_input(t_mod, model):
    with pytest.raises(ValueError, match="model IDs|control characters"):
        t_mod._config_text("", defaults("claude", {"claude": model}))


def test_config_refuses_unknown_tool(t_mod):
    with pytest.raises(ValueError, match="choose claude or codex"):
        t_mod._config_text("", defaults("cursor", {}))


def test_config_atomic_write_preserves_symlink_mode_and_concurrent_edits(t_mod, tmp_path, monkeypatch):
    target = tmp_path / "private.zsh"
    target.write_text("original\n")
    target.chmod(0o640)
    link = tmp_path / "local"
    link.symlink_to(target)
    t_mod._config_write(str(link), "original\n", "saved\n")
    assert link.is_symlink() and target.read_text() == "saved\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    with pytest.raises(ValueError, match="changed while"):
        t_mod._config_write(str(link), "original\n", "overwrite\n")
    assert target.read_text() == "saved\n"
    # A failed replacement leaves the old file intact and cleans the temp file.
    def fail(*args):
        raise OSError("read-only")
    monkeypatch.setattr(t_mod.os, "replace", fail)
    with pytest.raises(OSError):
        t_mod._config_write(str(link), "saved\n", "overwrite\n")
    assert target.read_text() == "saved\n" and not list(tmp_path.glob(".t-config-*"))


def test_config_creates_missing_local_file(t_mod, tmp_path):
    target = tmp_path / "local"
    assert t_mod._config_read(target) == ""
    t_mod._config_write(str(target), "", "new\n")
    assert target.read_text() == "new\n" and stat.S_IMODE(target.stat().st_mode) == 0o600


class Menu:
    def __init__(self, picks, inputs=(), pages=("y",)):
        self.picks, self.inputs, self.pages = iter(picks), iter(inputs), iter(pages)
        self.out = io.StringIO()
        self.restored = False

    def raw(self):
        pass

    def restore(self):
        self.restored = True

    def pick(self, label, rows, default=0):
        pick = next(self.picks)
        assert pick is None or pick in dict(rows), (label, pick, rows)
        assert 0 <= default < len(rows)
        return pick

    def cooked_input(self, prompt, **kwargs):
        return next(self.inputs)

    def page(self, *args, **kwargs):
        return next(self.pages)

    def done(self, *args):
        pass

    def commit(self, lines):
        self.out.write("\n".join(lines))


@pytest.fixture
def config_cli(t_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(t_mod, "CONFIG", str(tmp_path / "config.sh"))
    local = tmp_path / "local"
    local.write_text("# keep me\n")
    monkeypatch.setattr(t_mod, "ZSHRC_LOCAL", str(local))
    monkeypatch.setattr(t_mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(t_mod.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(t_mod, "_config_live_models", lambda agent: [
        {"value": "opus", "resolvedModel": "claude-test-opus", "supportsEffort": True,
         "supportedEffortLevels": ["low", "high", "max"]},
        {"value": "haiku", "resolvedModel": "claude-test-haiku"},
    ] if agent == "claude" else [
        {"model": "test-model", "supportedReasoningEfforts": [{"reasoningEffort": "high"},
                                                               {"reasoningEffort": "ultra"}]}])
    monkeypatch.setattr(t_mod, "zsh_capture", lambda snippet: "")
    return t_mod.Config(), local


def test_config_menu_save_then_cancel(t_mod, config_cli, monkeypatch):
    cfg, local = config_cli
    ui = Menu(["tool", "codex", "codex", "__custom__", "claude", "opus", "save"], ["local/model"])
    monkeypatch.setattr(t_mod, "_RailUI", lambda: ui)
    assert t_mod.cmd_config(cfg, SimpleNamespace(show=False)) == 0
    assert ui.restored
    text = local.read_text()
    assert text.startswith("# keep me\n") and "DEV_AGENT_DEFAULT=codex" in text
    assert "DEV_MODEL[codex]=local/model" in text and "DEV_MODEL[claude]=opus" in text
    ui = Menu(["tool", "claude", "cancel"])
    assert t_mod.cmd_config(cfg, SimpleNamespace(show=False)) == 0
    assert local.read_text() == text and ui.restored


def test_config_menu_custom_validation_back_reset_and_no_change(t_mod, config_cli, monkeypatch):
    cfg, local = config_cli
    cfg.models = {"codex": "old"}
    ui = Menu(["tool", None, "codex", "__custom__", "__custom__", "codex", "", "save"],
              ["bad model", ""])
    monkeypatch.setattr(t_mod, "_RailUI", lambda: ui)
    assert t_mod.cmd_config(cfg, SimpleNamespace(show=False)) == 0
    assert "DEV_MODEL[codex]=''" in local.read_text()
    before = local.stat()
    cfg.models = {}
    ui = Menu(["save"])
    assert t_mod.cmd_config(cfg, SimpleNamespace(show=False)) == 0
    assert local.stat() == before and ui.restored


def test_config_show_and_non_tty_never_write(t_mod, config_cli, monkeypatch, capsys):
    cfg, local = config_cli
    cfg.agents = {"api": "codex"}
    monkeypatch.setattr(t_mod.sys.stdin, "isatty", lambda: False)
    assert t_mod.cmd_config(cfg, SimpleNamespace(show=True)) == 0
    assert "api=codex" in capsys.readouterr().out
    assert t_mod.cmd_config(cfg, SimpleNamespace(show=False)) == 1
    assert "terminal" in capsys.readouterr().err
    assert local.read_text() == "# keep me\n"


def test_config_menu_interrupt_restores_terminal(t_mod, config_cli, monkeypatch):
    cfg, local = config_cli
    ui = Menu([])
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt
    ui.pick = interrupt
    monkeypatch.setattr(t_mod, "_RailUI", lambda: ui)
    with pytest.raises(KeyboardInterrupt):
        t_mod.cmd_config(cfg, SimpleNamespace(show=False))
    assert ui.restored and local.read_text() == "# keep me\n"


def test_config_removals_survive_other_edits_and_setup_can_reregister(t_mod):
    original = "REMOTE_HOSTS[retired]=old.example\nDEV_REPOS[api]=/code/api\nexport TBEAM_HOST=old.example\n"
    updated = t_mod._config_text(original, {"REMOTE_HOSTS[retired]": None,
                                "DEV_REPOS[api]": None, "TBEAM_HOST": ""})
    assert t_mod._local_entries(updated) == ({}, {}, False)
    again = t_mod._config_text(updated, {"DEV_MODEL[claude]": "opus"})
    assert t_mod._local_entries(again) == ({}, {}, False)
    # t setup appends a replacement after the overlay. A later unrelated save
    # reconciles the old tombstone with that effective registration.
    again += "REMOTE_HOSTS[retired]=new.example\n"
    final = t_mod._config_text(again, {"DEV_AGENT_DEFAULT": "codex"},
                              {"REMOTE_HOSTS[retired]": "new.example", "DEV_MODEL[claude]": "opus"})
    assert t_mod._local_entries(final) == ({}, {"retired": "new.example"}, False)
    assert "DEV_MODEL[claude]=opus" in final


def test_config_preserves_existing_defaults_blocks_and_rejects_custom_shell(t_mod):
    text = t_mod._config_text("", defaults("codex", {"codex": "local/model"}))
    text = t_mod._config_text(text, {"REMOTE_HOSTS[mini]": "user@mini.example"})
    assert "DEV_MODEL[codex]=local/model" in text
    assert "REMOTE_HOSTS[mini]=user@mini.example" in text
    with pytest.raises(ValueError, match="custom shell"):
        t_mod._config_text(text.replace("DEV_AGENT_DEFAULT=codex", "source private.zsh"), {})


@pytest.mark.parametrize("key,value", [("PATH", "/tmp"), ("REMOTE_HOSTS[$(id)]", "host"),
                                       ("DEV_REPOS[api]", "/x\ny"), ("DEV_BRANCH", 3)])
def test_config_rejects_unsafe_settings(t_mod, key, value):
    with pytest.raises(ValueError):
        t_mod._config_text("", {key: value})


def test_config_host_removal_clears_related_defaults(t_mod, config_cli):
    cfg, _ = config_cli
    cfg.hosts = {"retired": "old.example", "mini": "mini.example"}
    cfg.beam_host, cfg.mini_host = "retired", "old.example"
    t_mod._config_remove_host(cfg, "retired")
    assert cfg.hosts == {"mini": "mini.example"} and cfg.beam_host == cfg.mini_host == ""
    cfg.beam_host, cfg.mini_host = "stale.example", "legacy.example"
    t_mod._config_remove_host(cfg, "mini")
    assert not cfg.hosts and cfg.beam_host == cfg.mini_host == ""


def test_config_hosts_remove_save_and_cancel(t_mod, config_cli, monkeypatch):
    cfg, local = config_cli
    cfg.hosts = {"retired": "old.example", "mini": "mini.example"}
    cfg.beam_host = "old.example"
    cfg.mini_host = "old.example"
    local.write_text("REMOTE_HOSTS[retired]=old.example\nREMOTE_HOSTS[mini]=mini.example\n"
                     "export TBEAM_HOST=old.example\nMINI_HOST=old.example\n")
    before = local.read_text()
    ui = Menu(["hosts", "retired", "remove", "__back__", "cancel"])
    monkeypatch.setattr(t_mod, "_RailUI", lambda: ui)
    assert t_mod.cmd_config(cfg, SimpleNamespace(show=False)) == 0
    assert local.read_text() == before and "retired" in cfg.hosts
    ui = Menu(["hosts", "retired", "remove", "__back__", "save"])
    assert t_mod.cmd_config(cfg, SimpleNamespace(show=False)) == 0
    assert t_mod._local_entries(local.read_text()) == ({}, {"mini": "mini.example"}, False)
    assert "unset MINI_HOST" in local.read_text()


def test_config_hosts_add_edit_and_default(t_mod, config_cli, monkeypatch):
    cfg, local = config_cli
    ui = Menu(["hosts", "__add__", "lab", "beam", "lab", "target", "__back__", "save"],
              ["lab", "old.example", "new.example"])
    monkeypatch.setattr(t_mod, "_RailUI", lambda: ui)
    assert t_mod.cmd_config(cfg, SimpleNamespace(show=False)) == 0
    text = local.read_text()
    assert "REMOTE_HOSTS[lab]=new.example" in text and "export TBEAM_HOST=new.example" in text


def test_config_repos_edit_overrides_unregister_and_add(t_mod, config_cli, monkeypatch, tmp_path):
    cfg, local = config_cli
    cfg.repos = {"api": "/old/api", "retired": "/old/retired"}
    cfg.agents = {"retired": "codex"}
    cfg.branches = {"retired": "main"}
    cfg.worktree = {"retired": "0"}
    ui = Menu(["repos", "api", "tool", "codex", "api", "worktree", "0",
               "api", "branch", "custom", "api", "path", "retired", "remove",
               "__add__", "__back__", "save"], ["dev/custom", str(tmp_path / "new api"), "web", "/code/web"])
    monkeypatch.setattr(t_mod, "_RailUI", lambda: ui)
    assert t_mod.cmd_config(cfg, SimpleNamespace(show=False)) == 0
    text = local.read_text()
    assert "DEV_AGENT[api]=codex" in text and "DEV_WORKTREE[api]=0" in text
    assert "DEV_BRANCHES[api]=dev/custom" in text and "DEV_REPOS[web]=/code/web" in text
    for key in ("DEV_REPOS", "DEV_AGENT", "DEV_BRANCHES", "DEV_WORKTREE"):
        assert f"unset '{key}[retired]'" in text


def test_config_worktree_defaults_and_beam_clear(t_mod, config_cli, monkeypatch, tmp_path):
    cfg, local = config_cli
    cfg.beam_host = "old.example"
    ui = Menu(["worktrees", "enabled", "0", "root", "branch", "back", "beam", "", "save"],
              [str(tmp_path / "trees"), "dev/shared"])
    monkeypatch.setattr(t_mod, "_RailUI", lambda: ui)
    assert t_mod.cmd_config(cfg, SimpleNamespace(show=False)) == 0
    text = local.read_text()
    assert "DEV_WORKTREE_DEFAULT=0" in text and "DEV_BRANCH=dev/shared" in text
    assert "unset TBEAM_HOST" in text


def test_config_review_can_back_out_or_cancel(t_mod, config_cli, monkeypatch):
    cfg, local = config_cli
    ui = Menu(["tool", "codex", "save", "save"], pages=["n", "q"])
    monkeypatch.setattr(t_mod, "_RailUI", lambda: ui)
    assert t_mod.cmd_config(cfg, SimpleNamespace(show=False)) == 0
    assert local.read_text() == "# keep me\n"


def test_config_editor_uses_argument_list_and_checks_syntax(t_mod, monkeypatch):
    monkeypatch.setenv("VISUAL", "my-editor --wait")
    calls = []
    monkeypatch.setattr(t_mod.subprocess, "call", lambda argv: calls.append(argv) or 0)
    assert t_mod._config_editor() == 0
    assert calls == [["my-editor", "--wait", t_mod.ZSHRC_LOCAL], ["zsh", "-n", t_mod.ZSHRC_LOCAL]]
    monkeypatch.setattr(t_mod.subprocess, "call", lambda argv: 7)
    assert t_mod._config_editor() == 7


def test_config_editor_requires_pending_changes_to_be_saved(t_mod, config_cli, monkeypatch):
    cfg, local = config_cli
    ui = Menu(["tool", "codex", "edit", "cancel"])
    monkeypatch.setattr(t_mod, "_RailUI", lambda: ui)
    monkeypatch.setattr(t_mod, "_config_editor", lambda: pytest.fail("must not open editor"))
    assert t_mod.cmd_config(cfg, SimpleNamespace(show=False)) == 0
    assert local.read_text() == "# keep me\n"


def test_config_selects_and_persists_model_specific_effort(t_mod, config_cli, monkeypatch):
    cfg, local = config_cli
    ui = Menu(["claude", "opus", "effort:claude", "max",
               "codex", "test-model", "effort:codex", "ultra", "save"])
    monkeypatch.setattr(t_mod, "_RailUI", lambda: ui)
    assert t_mod.cmd_config(cfg, SimpleNamespace(show=False)) == 0
    text = local.read_text()
    assert "DEV_EFFORT[claude]=max" in text and "DEV_EFFORT[codex]=ultra" in text
    assert "DEV_MODEL[claude]=opus" in text and "DEV_MODEL[codex]=test-model" in text


def test_config_switching_to_non_effort_model_clears_effort(t_mod, config_cli, monkeypatch):
    cfg, local = config_cli
    cfg.models, cfg.efforts = {"claude": "opus"}, {"claude": "max"}
    ui = Menu(["claude", "haiku", "effort:claude", "", "save"])
    monkeypatch.setattr(t_mod, "_RailUI", lambda: ui)
    assert t_mod.cmd_config(cfg, SimpleNamespace(show=False)) == 0
    assert "DEV_EFFORT[claude]=''" in local.read_text()


def test_config_model_lookup_is_lazy_and_refreshable(t_mod, config_cli, monkeypatch):
    cfg, local = config_cli
    calls = []
    def discover(agent):
        calls.append(agent)
        if len(calls) == 1:
            raise ValueError("offline")
        return [{"model": "new-model", "supportedReasoningEfforts": [{"reasoningEffort": "ultra"}]}]
    monkeypatch.setattr(t_mod, "_config_live_models", discover)
    ui = Menu(["cancel"])
    monkeypatch.setattr(t_mod, "_RailUI", lambda: ui)
    assert t_mod.cmd_config(cfg, SimpleNamespace(show=False)) == 0 and not calls
    ui = Menu(["codex", "__refresh__", "new-model", "effort:codex", "ultra", "save"])
    assert t_mod.cmd_config(cfg, SimpleNamespace(show=False)) == 0
    assert calls == ["codex", "codex"]  # one retry, then reuse within this menu
    assert "DEV_MODEL[codex]=new-model" in local.read_text()


def test_config_unavailable_metadata_keeps_custom_and_native_choices(t_mod, config_cli, monkeypatch):
    cfg, local = config_cli
    def unavailable(agent):
        raise FileNotFoundError(agent)
    monkeypatch.setattr(t_mod, "_config_live_models", unavailable)
    ui = Menu(["codex", "__custom__", "effort:codex", "", "save"], ["private/model"])
    monkeypatch.setattr(t_mod, "_RailUI", lambda: ui)
    assert t_mod.cmd_config(cfg, SimpleNamespace(show=False)) == 0
    assert "DEV_MODEL[codex]=private/model" in local.read_text()
