"""Unit tests for `t install` (bin/t): the agent table, the probe classifiers, the
checklist item model, the pure plan, its rendering, the host/ssh helpers, the
parity matrix (pinned to README), the codex hook-state reader, and the doctor
findings it feeds. The tty loop / subprocess walk are glue, excluded from coverage.
"""

import json
import os
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


class _St:
    g = c = y = b = r = ""


def _probed(**kw):
    """A probe result: every agent missing unless overridden with
    (version, logged_in) tuples."""
    out = {a: {"bin": None, "version": None, "logged_in": None}
           for a in ("claude", "codex", "cursor")}
    for a, (ver, li) in kw.items():
        out[a] = {"bin": "/x/" + a, "version": ver, "logged_in": li}
    return out


# ─── the agent table ────────────────────────────────────────────────────────────

def test_install_agents_table_is_complete(t_mod):
    assert t_mod.INSTALL_AGENTS == ("claude", "codex", "cursor")
    for a in t_mod.INSTALL_AGENTS:
        spec = t_mod._INSTALL_AGENTS[a]
        assert spec["bin"] and spec["label"]
        assert {"darwin", "linux"} <= set(spec["install"])
        for key in ("login", "login_headless", "status", "update"):
            assert isinstance(spec[key], list) and spec[key], (a, key)
        # every login/status/update command starts with the agent's own binary
        # (or env for the headless cursor variant)
        for key in ("login", "login_headless", "status", "update"):
            assert spec[key][0] in (spec["bin"], "env"), (a, key)


def test_install_cmd_picks_the_os_variant(t_mod):
    label, argv = t_mod._install_cmd("codex", "darwin", has_brew=True)
    assert label == "brew install --cask codex"
    assert argv == ["sh", "-c", label]
    label, _ = t_mod._install_cmd("codex", "darwin", has_brew=False)
    assert label.startswith("curl") and "chatgpt.com/codex/install.sh" in label
    label, _ = t_mod._install_cmd("codex", "linux")
    assert "chatgpt.com/codex/install.sh" in label
    # claude/cursor have no brew path: same script on both OSes
    assert t_mod._install_cmd("claude", "darwin")[0] == t_mod._install_cmd("claude", "linux")[0]
    assert "cursor.com/install" in t_mod._install_cmd("cursor", "linux")[0]


def test_install_login_cmd_headless_variants(t_mod):
    assert t_mod._install_login_cmd("codex", False) == ["codex", "login"]
    assert t_mod._install_login_cmd("codex", True) == ["codex", "login", "--device-auth"]
    assert t_mod._install_login_cmd("cursor", True)[:2] == ["env", "NO_OPEN_BROWSER=1"]
    assert t_mod._install_login_cmd("claude", True) == ["claude", "auth", "login"]


def test_install_headless_from_env_or_flag(t_mod):
    assert not t_mod._install_headless({})
    assert t_mod._install_headless({"SSH_CONNECTION": "1.2.3.4 1 5.6.7.8 22"})
    assert t_mod._install_headless({"SSH_TTY": "/dev/ttys1"})
    assert t_mod._install_headless({}, flag=True)


def test_install_version_parses_each_vendor_format(t_mod):
    assert t_mod._install_version("2.1.267 (Claude Code)\n") == "2.1.267"
    assert t_mod._install_version("codex-cli 0.153.4") == "0.153.4"
    assert t_mod._install_version("2026.02.13-41ac335\n") == "2026.02.13-41ac335"
    assert t_mod._install_version("") is None
    assert t_mod._install_version("no digits here") == "no digits here"


# ─── status classifiers ────────────────────────────────────────────────────────

def test_install_status_ok_claude_reads_json(t_mod):
    assert t_mod._install_status_ok("claude", 0, json.dumps({"loggedIn": True, "authMethod": "claude.ai"}))
    assert not t_mod._install_status_ok("claude", 0, json.dumps({"loggedIn": False}))
    # a wrapped / noisy JSON still matches the key; garbage does not
    assert t_mod._install_status_ok("claude", 0, 'noise\n{"loggedIn": true}')
    assert not t_mod._install_status_ok("claude", 1, "")


def test_install_status_ok_codex_text(t_mod):
    assert t_mod._install_status_ok("codex", 0, "Logged in using ChatGPT\n")
    assert not t_mod._install_status_ok("codex", 0, "Not logged in\n")
    assert not t_mod._install_status_ok("codex", 1, "Logged in using ChatGPT")


def test_install_status_ok_cursor_strips_ansi_and_animation(t_mod):
    raw = ("\x1b[2K\x1b[G\n Starting login process...\n\n\x1b[2K\x1b[1A"
           "Checking authentication status...\n\n ✓ Login successful!\n"
           " Logged in (unable to fetch user details)\n")
    assert t_mod._install_status_ok("cursor", 0, raw)
    assert not t_mod._install_status_ok("cursor", 0, "You are not logged in. Run cursor-agent login")
    assert not t_mod._install_status_ok("cursor", 0, "")


# ─── the item model ────────────────────────────────────────────────────────────

def test_install_items_locks_present_and_premarks_missing(t_mod):
    items = t_mod._install_items(_probed(claude=("2.1.0", True)), None, [])
    assert items[0] == {"t": "header", "sec": "agents", "label": "AGENTS · install"}
    agents = [(it["t"], it.get("kind"), it["alias"]) for it in items if it.get("sec") == "agents" and it["t"] != "header"]
    assert agents == [("locked", None, "claude"), ("toggle", "install", "codex"),
                      ("toggle", "install", "cursor")]
    login = [(it["t"], it["alias"], it.get("checked")) for it in items
             if it.get("sec") == "login" and it["t"] in ("locked", "toggle")]
    assert login == [("locked", "claude", None), ("toggle", "codex", True), ("toggle", "cursor", True)]
    # a locked row carries the version so the list explains itself
    assert next(it for it in items if it["t"] == "locked")["value"] == "2.1.0 · installed"
    # OPTIONS (update) exists because something is installed; no HOSTS section
    assert any(it.get("kind") == "update" and not it["checked"] for it in items)
    assert not any(it.get("sec") == "hosts" for it in items)


def test_install_items_want_marks_only_the_named_agents(t_mod):
    items = t_mod._install_items(_probed(), ["codex"], [("mini", "me@mini")], update=True)
    marks = {it["alias"]: it["checked"] for it in items if it.get("kind") == "install"}
    assert marks == {"claude": False, "codex": True, "cursor": False}
    logins = {it["alias"]: it["checked"] for it in items if it.get("kind") == "login"}
    assert logins == {"claude": False, "codex": True, "cursor": False}
    # nothing installed → no OPTIONS section even with update=True
    assert not any(it.get("kind") == "update" for it in items)
    hosts = [(it["alias"], it["value"], it["checked"]) for it in items if it.get("kind") == "host"]
    assert hosts == [("mini", "me@mini", True)]


def test_install_items_no_login_unmarks_logins(t_mod):
    items = t_mod._install_items(_probed(codex=("0.1", False)), None, [], no_login=True)
    assert all(not it["checked"] for it in items if it.get("kind") == "login")
    # an installed-but-logged-out agent still gets a login TOGGLE, not a lock
    assert any(it.get("kind") == "login" and it["alias"] == "codex" for it in items)


def test_install_result_collects_checked_toggles(t_mod):
    items = t_mod._install_items(_probed(claude=("1", True)), None, [("mini", "m"), ("box", "b")])
    for it in items:
        if it.get("kind") == "host" and it["alias"] == "box":
            it["checked"] = False
        if it.get("kind") == "update":
            it["checked"] = True
    sel = t_mod._install_result(items)
    assert sel == {"install": ["codex", "cursor"], "login": ["codex", "cursor"],
                   "update": True, "hosts": ["mini"]}
    assert t_mod._selectable(items)   # the shared selectable() works on this model too


# ─── the plan ──────────────────────────────────────────────────────────────────

def test_install_plan_full_matrix(t_mod):
    probed = _probed(claude=("2.1", True), codex=("0.15", False))
    sel = {"install": ["claude", "codex", "cursor"], "login": ["claude", "codex", "cursor"],
           "update": False, "hosts": []}
    steps = t_mod._install_plan(sel, probed, "darwin", True, False, [])
    by = {s["step"]: s for s in steps}
    assert by["install:claude"]["do"] == "skip" and "2.1" in by["install:claude"]["label"]
    assert by["login:claude"]["do"] == "skip"
    assert by["install:codex"]["do"] == "skip"
    assert by["login:codex"]["do"] == "run" and by["login:codex"]["cmd"] == ["codex", "login"]
    assert by["login:codex"]["interactive"] is True
    assert by["install:cursor"]["do"] == "run" and by["install:cursor"]["cmd"][0] == "sh"
    assert by["login:cursor"]["do"] == "run"
    # order: each agent's install precedes its login; agents in table order
    assert [s["step"] for s in steps] == ["install:claude", "login:claude", "install:codex",
                                          "login:codex", "install:cursor", "login:cursor"]


def test_install_plan_codex_install_carries_the_hook_note_and_headless_login(t_mod):
    sel = {"install": ["codex"], "login": ["codex"], "update": False, "hosts": []}
    steps = t_mod._install_plan(sel, _probed(), "linux", False, True, [])
    inst, login = steps
    assert inst["do"] == "run" and "chatgpt.com/codex/install.sh" in inst["label"]
    assert "hooks prompt" in inst["warn"]
    assert login["cmd"] == ["codex", "login", "--device-auth"]


def test_install_plan_login_without_binary_is_an_error(t_mod):
    sel = {"install": [], "login": ["cursor"], "update": False, "hosts": []}
    steps = t_mod._install_plan(sel, _probed(), "darwin", True, False, [])
    assert [(s["step"], s["do"]) for s in steps] == [("login:cursor", "error")]
    assert "not installed" in steps[0]["label"]


def test_install_plan_update_only_for_installed_unselected_agents(t_mod):
    probed = _probed(claude=("2.1", True), cursor=("2026.1", True))
    sel = {"install": ["codex"], "login": [], "update": True, "hosts": []}
    steps = t_mod._install_plan(sel, probed, "darwin", True, False, [])
    assert [s["step"] for s in steps] == ["update:claude", "install:codex", "update:cursor"]
    assert steps[0]["cmd"] == ["claude", "update"] and steps[0]["do"] == "run"


def test_install_plan_hosts_get_the_same_verb_over_ssh(t_mod):
    sel = {"install": ["codex"], "login": ["codex"], "update": True, "hosts": ["mini"]}
    hosts = [("mini", "me@mini"), ("box", "me@box")]
    steps = t_mod._install_plan(sel, _probed(), "darwin", True, False, hosts)
    host_steps = [s for s in steps if s["step"].startswith("host:")]
    assert [s["host"] for s in host_steps] == ["mini"]     # box was not selected
    argv = host_steps[0]["cmd"]
    assert argv[:2] == ["ssh", "-t"] and argv[-2] == "me@mini"
    assert "t install codex -y --no-hosts --update" in argv[-1]
    assert host_steps[0]["interactive"] is True


def test_install_ssh_argv_quotes_two_layers(t_mod):
    argv = t_mod._install_ssh_argv("me@mini", ["codex", "cursor"], no_login=True)
    assert argv[0] == "ssh" and "-t" in argv
    assert argv[-1] == "zsh -lic 't install codex cursor -y --no-hosts --no-login'"


def test_install_host_outcome_and_line(t_mod):
    assert t_mod._install_host_outcome(0) == "ok"
    assert t_mod._install_host_outcome(255) == "unreachable"
    assert t_mod._install_host_outcome(124) == "unreachable"
    assert t_mod._install_host_outcome(2) == "stale"
    assert t_mod._install_host_outcome(127) == "missing"
    assert t_mod._install_host_outcome(1) == "failed"
    assert t_mod._install_host_line("mini", "ok").startswith("✓ mini")
    assert "dots" in t_mod._install_host_line("mini", "stale")
    assert "install.sh" in t_mod._install_host_line("mini", "missing")
    assert "--hosts mini" in t_mod._install_host_line("mini", "unreachable")
    assert t_mod._install_host_line("mini", "failed").startswith("⚠ mini")


def test_install_render_marks_and_host_trailer(t_mod):
    probed = _probed(claude=("2.1", True))
    sel = {"install": ["codex"], "login": ["claude", "codex", "cursor"], "update": False,
           "hosts": ["mini"]}
    steps = t_mod._install_plan(sel, probed, "darwin", True, False, [("mini", "me@mini")])
    lines = t_mod._install_render(steps, _St())
    assert lines[0] == "= claude already logged in"
    assert lines[1] == "+ brew install --cask codex"
    assert lines[2].startswith("  ⚠ then start codex")
    assert lines[3] == "+ codex login"
    assert lines[4].startswith("✗ cursor login: not installed")
    # the hosts get every agent named for install OR login: a login the local box did
    # not need may still be missing there
    assert lines[-1] == "→ then on mini: t install claude codex cursor -y --no-hosts"


def test_install_render_empty_when_nothing_to_do(t_mod):
    sel = {"install": [], "login": [], "update": False, "hosts": []}
    assert t_mod._install_render(t_mod._install_plan(sel, _probed(), "darwin", True, False, []), _St()) == []


# ─── parity matrix ─────────────────────────────────────────────────────────────

def test_agent_parity_table_shape(t_mod):
    rows = t_mod._AGENT_PARITY
    assert rows[0] == ("surface", "claude", "codex", "cursor")
    assert all(len(r) == 4 and all(isinstance(c, str) and c for c in r) for r in rows)
    lines = t_mod._agent_parity_render()
    assert len(lines) == len(rows) + 1               # + the header rule
    assert "\x1b" not in "".join(lines)
    assert lines[0].startswith("  surface") and set(lines[1].strip()) == {"-", " "}


def test_readme_parity_matrix_matches_bin_t(t_mod):
    # the README carries the rendered matrix verbatim inside its Agents section: a new
    # surface (row) cannot ship without the documentation moving with it
    text = (REPO_ROOT / "README.md").read_text()
    start = text.index("## Agents")
    fence = text.index("```text\n", start) + len("```text\n")
    end = text.index("```", fence)
    assert text[fence:end].rstrip("\n") == "\n".join(t_mod._agent_parity_render())


def test_parity_matrix_is_in_the_help_epilog(t_mod):
    epilog = t_mod.build_parser().epilog
    for ln in t_mod._agent_parity_render():
        assert ln in epilog


# ─── codex hook state + doctor findings ────────────────────────────────────────

def test_codex_hook_state(t_mod, tmp_path):
    p = tmp_path / "hooks.json"
    assert t_mod._codex_hook_state(str(p)) == "none"          # absent
    p.write_text("{not json")
    assert t_mod._codex_hook_state(str(p)) is None            # unreadable → not ours
    p.write_text(json.dumps({"hooks": {"Stop": []}}))
    assert t_mod._codex_hook_state(str(p)) == "none"          # other events only
    p.write_text(json.dumps({"hooks": {"SessionStart": [
        {"matcher": "startup", "hooks": [{"type": "command", "command": "python3 x.py"}]},
        {"hooks": [{"type": "command", "command": "$HOME/bin/claude-stamp-tmux --agent codex"}]}]}}))
    assert t_mod._codex_hook_state(str(p)) == "registered"
    p.write_text(json.dumps({"hooks": {"SessionStart": "nope"}}))
    assert t_mod._codex_hook_state(str(p)) is None            # foreign shape
    p.write_text(json.dumps({"hooks": []}))
    assert t_mod._codex_hook_state(str(p)) is None


def test_doctor_findings_agent_not_logged_in(t_mod):
    out = t_mod._doctor_findings({"agents": _probed(codex=("0.15", False), claude=("2", True))})
    assert any("codex is installed but not logged in — run t install codex" in l for l in out)
    assert not any("claude is installed but not logged in" in l for l in out)


def test_doctor_findings_missing_agent_is_not_a_finding(t_mod):
    # not everyone wants all three; the Agents section shows it, findings stay quiet
    assert t_mod._doctor_findings({"agents": _probed(claude=("2", True))}) == ["✓ nothing suspicious found"]


def test_doctor_findings_codex_hook_states(t_mod):
    out = t_mod._doctor_findings({"codex_hook": "none"})
    assert any("no dotfiles SessionStart hook" in l and "run dots" in l for l in out)
    out = t_mod._doctor_findings({"codex_hook": "registered", "codex_hook_trusted": False})
    assert any("not trusted yet" in l and "Trust all and continue" in l for l in out)
    # trusted, or trust unknown (config unreadable) → quiet
    assert t_mod._doctor_findings({"codex_hook": "registered", "codex_hook_trusted": True}) == ["✓ nothing suspicious found"]
    assert t_mod._doctor_findings({"codex_hook": "registered", "codex_hook_trusted": None}) == ["✓ nothing suspicious found"]
    assert t_mod._doctor_findings({"codex_hook": None}) == ["✓ nothing suspicious found"]


def test_codex_hook_trusted_reads_the_config_record(t_mod, tmp_path):
    cfg = tmp_path / "config.toml"
    assert t_mod._codex_hook_trusted(str(cfg)) is None                      # no file
    cfg.write_text('model = "gpt-6"\n[hooks.state]\n')
    assert t_mod._codex_hook_trusted(str(cfg)) is False                     # no record
    cfg.write_text('[hooks.state]\n\n[hooks.state."/Users/me/.codex/hooks.json:session_start:0:0"]\n'
                   'trusted_hash = "sha256:547e29d0"\n')
    assert t_mod._codex_hook_trusted(str(cfg)) is True
    # a trust record for some OTHER hook source is not ours
    cfg.write_text('[hooks.state."/repo/.codex/config.toml:pre_tool_use:0:0"]\ntrusted_hash = "sha256:x"\n')
    assert t_mod._codex_hook_trusted(str(cfg)) is False
