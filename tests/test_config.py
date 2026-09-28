"""Session defaults: safe persistence, picker choices, and save/cancel behavior."""

import io
import json
import stat
from types import SimpleNamespace

import pytest


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
    updated = t_mod._config_text(original, "codex", models)
    assert updated.startswith(original + "\n\n")
    assert "DEV_MODEL[claude]='sonnet[1m]'" in updated
    assert t_mod._config_text(updated, "codex", models) == updated
    # Hand-written content appended later is preserved; our latest choice goes last.
    updated += "DEV_AGENT_DEFAULT=claude\n"
    cleared = t_mod._config_text(updated, "claude", {})
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
        t_mod._config_text(text, "claude", {})


@pytest.mark.parametrize("model", ["a b", "$(touch /tmp/no)", "x\ny", "--help", "a;exit", "\x1b[31m"])
def test_config_rejects_non_model_input(t_mod, model):
    with pytest.raises(ValueError, match="model IDs"):
        t_mod._config_text("", "claude", {"claude": model})


def test_config_refuses_unknown_tool(t_mod):
    with pytest.raises(ValueError, match="choose claude or codex"):
        t_mod._config_text("", "cursor", {})


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


def test_config_model_choices_handle_bad_or_missing_cache(t_mod):
    cache = {"models": [None, {}, {"slug": "bad name", "visibility": "list"},
                        {"slug": "hidden", "visibility": "hide"},
                        {"slug": "local/model", "visibility": "list"},
                        {"slug": "local/model", "visibility": "list"}]}
    choices = t_mod._config_model_rows("codex", "saved-model", cache)
    assert [row[0] for row in choices] == ["", "saved-model", "local/model", "__custom__"]
    for bad in (None, [], {}, {"models": "bad"}):
        assert [r[0] for r in t_mod._config_model_rows("codex", "", bad)] == ["", "__custom__"]
    assert [r[0] for r in t_mod._config_model_rows("claude", "opus")].count("opus") == 1


def test_config_cache_honors_codex_home(t_mod, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert t_mod._config_codex_cache() is None
    cache = tmp_path / "models_cache.json"
    cache.write_text("invalid json")
    assert t_mod._config_codex_cache() is None
    cache.write_text(json.dumps({"models": []}))
    assert t_mod._config_codex_cache() == {"models": []}


class Menu:
    def __init__(self, picks, inputs=()):
        self.picks, self.inputs = iter(picks), iter(inputs)
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

    def cooked_input(self, prompt):
        return next(self.inputs)

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
    monkeypatch.setattr(t_mod, "_config_codex_cache", lambda: None)
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
    ui = Menu(["tool", None, "codex", "__custom__", "codex", "__custom__", "codex", "", "save"],
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
