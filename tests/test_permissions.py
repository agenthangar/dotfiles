"""agents/permissions.allow → each agent's own config (`t permissions`, and the
install.sh step every `dots` runs). Covers the pure parser / translators / planners
in bin/t, the per-agent sync over a sandbox HOME, the shipped lists' invariants, the
doctor finding, and install.sh's links-only call end to end — the codex-hooks test
shape: a seeded checkout carrying the REAL bin/t + agents/ under a throwaway HOME,
PATH narrowed so the developer's real codex cannot flip the gate."""

import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from test_install_migration import _seed_worktree, git

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

ALLOW = ["Bash(gh pr:*)", "Bash(npx playwright *)", "WebSearch", "mcp__x__y"]
SHIPPED_GENERIC = {"WebSearch", "DesignSync", "mcp__plugin_chrome-devtools-mcp_chrome-devtools__*"}
RETIRE = ["Bash(old:*)"]


# ─── parsing + translation ──────────────────────────────────────────────────────

def test_perm_parse_drops_comments_blanks_and_duplicates(t_mod):
    text = "# head\n\nBash(a:*)\n  Bash(b:*)  \n# mid\nBash(a:*)\n\nWebSearch\n"
    assert t_mod._perm_parse(text) == ["Bash(a:*)", "Bash(b:*)", "WebSearch"]
    assert t_mod._perm_parse("") == []


@pytest.mark.parametrize("rule,prefix", [
    ("Bash(gh pr:*)", ["gh", "pr"]),
    ("Bash(npx playwright *)", ["npx", "playwright"]),       # Claude's other wildcard spelling
    ("Bash(chmod +x:*)", ["chmod", "+x"]),
    ("Bash(.venv/bin/python3:*)", [".venv/bin/python3"]),
    ("Bash(brew --prefix:*)", ["brew", "--prefix"]),
    ("Bash(xxd)", None),                      # exact command: a prefix would widen it
    ("Bash(git commit -m ':*)", None),        # a quote: shell syntax
    ("Bash(PORT=3000 npm start:*)", None),    # env assignment, not an argv
    ("Bash(DATABASE_URL=* node *)", None),     # env assignment: cursor + claude only
    ("Bash(find . -name *.ts:*)", None),      # glob
    ("Bash(echo $HOME:*)", None),
    ("Bash(cat a | b:*)", None),
    ("Bash(ls ~/x:*)", None),
    ("Bash(:*)", None),
    ("Bash( *)", None),
    ("WebSearch", None),
    ("WebFetch(domain:github.com)", None),
    ("mcp__x__y", None),
    ("Skill(update-config)", None),
])
def test_perm_bash_prefix(t_mod, rule, prefix):
    assert t_mod._perm_bash_prefix(rule) == prefix


@pytest.mark.parametrize("rule,parsed", [
    ("Bash(gh pr:*)", ([], ["gh", "pr"])),
    ("Bash(DATABASE_URL=* node *)", ([("DATABASE_URL", "*")], ["node"])),
    ("Bash(DATABASE_URL=* node:*)", None),     # the `:*` spelling: Claude accepts it, never matches it
    ("Bash(*=* node:*)", None),
    ("Bash(*=* npm run *)", ([("*", "*")], ["npm", "run"])),          # any variable
    ("Bash(PORT=3000 npm start *)", ([("PORT", "3000")], ["npm", "start"])),
    ("Bash(A=1 B=* npm run *)", ([("A", "1"), ("B", "*")], ["npm", "run"])),
    ("Bash(A=$X node *)", None),               # an expansion in the value
    ("Bash(A=b:c node *)", None),              # a colon: cursor splits its pattern on it
    ("Bash(A='x y' node *)", None),            # a quote
    ("Bash(DATABASE_URL=* *)", None),          # an assignment with no command after it
    ("Bash(DATABASE_URL=*:*)", None),
    ("Bash(DATABASE_URL=* node)", None),       # exact, not a prefix rule
    ("Bash(1A=x node *)", None),               # not a variable name
    ("Bash(xxd)", None),
    ("WebSearch", None),
])
def test_perm_bash_rule(t_mod, rule, parsed):
    assert t_mod._perm_bash_rule(rule) == parsed


def test_perm_translate_per_agent(t_mod):
    rules = ["Bash(awk *)", "Bash(awk:*)", "Bash(gh pr:*)", "Bash(xxd)", "WebSearch", "mcp__x__y",
             "Bash(DATABASE_URL=* node *)", "Bash(*=* node *)", "Bash(PORT=3000 npm start *)"]
    assert t_mod._perm_translate(rules, "claude") == rules
    # codex: argv prefixes only — an assignment is one opaque token to its rules
    assert t_mod._perm_translate(rules, "codex") == [
        'prefix_rule(pattern=["awk"], decision="allow")',
        'prefix_rule(pattern=["gh", "pr"], decision="allow")']
    # cursor: the assignment rides along as a whole-line glob, never the `:*` spelling
    assert t_mod._perm_translate(rules, "cursor") == [
        "Shell(awk)", "Shell(gh pr)", "Shell(DATABASE_URL=* node *)", "Shell(*=* node *)",
        "Shell(PORT=3000 npm start *)"]


def test_perm_lint_refuses_the_colon_spelling_of_an_env_rule(t_mod):
    assert t_mod._perm_lint(["Bash(gh pr:*)", "Bash(*=* node *)", "Bash(xxd)", "WebSearch"]) == []
    bad = t_mod._perm_lint(["Bash(DATABASE_URL=* node:*)", "Bash(*=* npm run:*)"])
    assert [r for r, _ in bad] == ["Bash(DATABASE_URL=* node:*)", "Bash(*=* npm run:*)"]
    assert "Bash(DATABASE_URL=* node *)" in bad[0][1] and "Bash(*=* npm run *)" in bad[1][1]


def test_perm_cursor_rule_is_what_its_matcher_takes():
    """cursor's matchGlob (2026.02 bundle): `^` + escaped pattern with `\\*` → `.*` + `$`
    over the full command text; a plain prefix is startsWith('<words> ')."""
    import re

    def match_glob(pattern, text):
        return re.match("^%s$" % re.escape(pattern.strip()).replace(r"\*", ".*"), text) is not None
    g = "DATABASE_URL=* node *"
    assert match_glob(g, "DATABASE_URL=postgres://u:p@localhost:55435/db node node_modules/vitest/vitest.mjs run x")
    assert match_glob(g, "DATABASE_URL='' node -e 1")
    assert not match_glob(g, "node -e 1") and not match_glob(g, "DATABASE_URL=x npm test")
    assert match_glob("*=* node *", "TEST_PG_PORT=55433 node x") and not match_glob("*=* node *", "node x")


def test_perm_codex_file_is_header_plus_rules(t_mod):
    text = t_mod._perm_codex_file(["Bash(gh pr:*)", "WebSearch"])
    assert text.startswith("# Managed by dotfiles")
    assert text.endswith('prefix_rule(pattern=["gh", "pr"], decision="allow")\n')
    assert "WebSearch" not in text
    # the rules file parses with the same parser (comments dropped) — the codex sync's diff relies on it
    assert t_mod._perm_parse(text) == ['prefix_rule(pattern=["gh", "pr"], decision="allow")']


def test_perm_list_plan(t_mod):
    current = ["Bash(mine:*)", "Bash(old:*)", {"odd": 1}, "Bash(gh pr:*)", "Bash(old:*)"]
    new, added, removed = t_mod._perm_list_plan(current, ALLOW, RETIRE)
    assert new == ["Bash(mine:*)", {"odd": 1}, "Bash(gh pr:*)",
                   "Bash(npx playwright *)", "WebSearch", "mcp__x__y"]
    assert added == ["Bash(npx playwright *)", "WebSearch", "mcp__x__y"]
    assert removed == ["Bash(old:*)", "Bash(old:*)"]         # every occurrence
    assert t_mod._perm_list_plan(new, ALLOW, RETIRE) == (new, [], [])   # a fixpoint


def test_perm_conflicts(t_mod):
    assert t_mod._perm_conflicts(["a", "b"], ["b", "c"]) == ["b"]
    assert t_mod._perm_conflicts(["a"], []) == []


# ─── the per-file syncs over tmp ───────────────────────────────────────────────

def test_perm_json_sync_states(t_mod, tmp_path):
    p = tmp_path / "settings.json"
    assert t_mod._perm_json_sync(str(p), ALLOW, RETIRE)["state"] == "missing"
    p.write_text("{ nope")
    assert t_mod._perm_json_sync(str(p), ALLOW, RETIRE)["state"] is None
    p.write_text("[]")
    assert t_mod._perm_json_sync(str(p), ALLOW, RETIRE)["state"] is None
    p.write_text(json.dumps({"permissions": {"allow": "Bash(x)"}}))
    assert t_mod._perm_json_sync(str(p), ALLOW, RETIRE)["state"] is None
    p.write_text(json.dumps({"permissions": "no"}))
    assert t_mod._perm_json_sync(str(p), ALLOW, RETIRE)["state"] is None
    # nothing above was written back
    assert p.read_text() == json.dumps({"permissions": "no"})

    seed = {"model": "fable", "hooks": {"Stop": []},
            "permissions": {"allow": ["Bash(old:*)", "Bash(mine:*)", "WebSearch"], "deny": ["Bash(rm:*)"],
                            "defaultMode": "auto"}}
    p.write_text(json.dumps(seed))
    rep = t_mod._perm_json_sync(str(p), ALLOW, RETIRE)
    assert rep["state"] == "pending"
    assert rep["add"] == ["Bash(gh pr:*)", "Bash(npx playwright *)", "mcp__x__y"]
    assert rep["retire"] == ["Bash(old:*)"]
    assert json.loads(p.read_text()) == seed                 # a report, not a write
    rep = t_mod._perm_json_sync(str(p), ALLOW, RETIRE, apply=True)
    assert rep["state"] == "applied"
    data = json.loads(p.read_text())
    assert data["permissions"]["allow"] == ["Bash(mine:*)", "WebSearch", "Bash(gh pr:*)",
                                            "Bash(npx playwright *)", "mcp__x__y"]
    for k in ("model", "hooks"):
        assert data[k] == seed[k]                               # the rest of the file survives
    assert data["permissions"]["deny"] == ["Bash(rm:*)"]
    assert data["permissions"]["defaultMode"] == "auto"
    assert p.read_text().endswith("}\n")
    assert not list(tmp_path.glob("*.tmp"))
    assert t_mod._perm_json_sync(str(p), ALLOW, RETIRE, apply=True)["state"] == "synced"


def test_perm_json_sync_creates_the_permissions_key(t_mod, tmp_path):
    p = tmp_path / "cli-config.json"
    p.write_text(json.dumps({"version": 1, "authInfo": {"email": "x@y"}}))
    assert t_mod._perm_json_sync(str(p), ["Shell(gh pr)"], [], apply=True)["state"] == "applied"
    data = json.loads(p.read_text())
    assert data == {"version": 1, "authInfo": {"email": "x@y"}, "permissions": {"allow": ["Shell(gh pr)"]}}


def test_perm_codex_sync(t_mod, tmp_path):
    p = tmp_path / ".codex" / "rules" / "dotfiles.rules"
    rep = t_mod._perm_codex_sync(str(p), ALLOW)
    assert rep["state"] == "pending" and not p.exists()
    assert rep["add"] == ['prefix_rule(pattern=["gh", "pr"], decision="allow")',
                          'prefix_rule(pattern=["npx", "playwright"], decision="allow")']
    rep = t_mod._perm_codex_sync(str(p), ALLOW, apply=True)
    assert rep["state"] == "applied"
    assert p.read_text() == t_mod._perm_codex_file(ALLOW)
    assert t_mod._perm_codex_sync(str(p), ALLOW, apply=True)["state"] == "synced"
    # ours, regenerated whole: a hand edit is reported and overwritten
    p.write_text(p.read_text() + 'prefix_rule(pattern=["mine"], decision="allow")\n')
    rep = t_mod._perm_codex_sync(str(p), ALLOW)
    assert rep["state"] == "pending" and rep["retire"] == ['prefix_rule(pattern=["mine"], decision="allow")']
    # a rule dropped from the list simply stops being generated
    rep = t_mod._perm_codex_sync(str(p), ALLOW[1:], apply=True)
    assert rep["state"] == "applied"
    assert 'pattern=["gh", "pr"]' not in p.read_text()


@pytest.mark.parametrize("have,expect", [
    ("", "append"),
    ('model = "gpt-6"\n', "append"),
    ('model = "gpt-6"', "append"),                                            # no trailing newline
    ('[sandbox_workspace_write]\nwritable_roots = []\n\n[foo]\na = 1\n', "insert"),
    ('[sandbox_workspace_write]  # hi\nnetwork_access = false\n', None),   # hand-set: never flipped
    ('[sandbox_workspace_write]\nnetwork_access = true\n', None),
    ('sandbox_workspace_write = { network_access = true }\n', None),        # inline table
    ('sandbox_workspace_write.network_access = false\n', None),             # dotted
    ('[profiles.x]\nnetwork_access = true\n', None),                       # any spelling anywhere: skip
])
def test_perm_codex_network_plan(t_mod, have, expect):
    out = t_mod._perm_codex_network_plan(have)
    if expect is None:
        assert out is None
        return
    tomllib = pytest.importorskip("tomllib")
    data = tomllib.loads(out)
    assert data["sandbox_workspace_write"]["network_access"] is True
    assert out.count("network_access") == 1 and out.endswith("\n")
    if expect == "insert":
        assert data["sandbox_workspace_write"]["writable_roots"] == [] and data["foo"]["a"] == 1
        assert out.index("network_access") < out.index("writable_roots")
    else:
        assert out.startswith(have.rstrip("\n")) and "[sandbox_workspace_write]\nnetwork_access = true" in out
        if have:
            assert tomllib.loads(have) == {k: v for k, v in data.items() if k != "sandbox_workspace_write"}


def test_perm_codex_network_sync(t_mod, tmp_path):
    p = tmp_path / ".codex" / "config.toml"
    rep = t_mod._perm_codex_network_sync(str(p))
    assert rep["state"] == "pending" and not p.exists()
    assert rep["add"] == ["config.toml: [sandbox_workspace_write] network_access = true"]
    rep = t_mod._perm_codex_network_sync(str(p), apply=True)
    assert rep["state"] == "applied" and "network_access = true" in p.read_text()
    assert t_mod._perm_codex_network_sync(str(p), apply=True) == {"state": "synced", "add": []}
    p.write_text('[sandbox_workspace_write]\nnetwork_access = false\n')
    assert t_mod._perm_codex_network_sync(str(p), apply=True)["state"] == "synced"
    assert p.read_text() == '[sandbox_workspace_write]\nnetwork_access = false\n'
    p.write_bytes(b"\xff\xfe not text")
    assert t_mod._perm_codex_network_sync(str(p), apply=True)["state"] is None
    assert p.read_bytes() == b"\xff\xfe not text"


def test_perm_codex_network_sync_never_writes_what_would_not_parse(t_mod, tmp_path, monkeypatch):
    p = tmp_path / "config.toml"
    p.write_text('model = "gpt-6"\n')
    monkeypatch.setattr(t_mod, "_perm_toml_ok", lambda text: False)
    rep = t_mod._perm_codex_network_sync(str(p), apply=True)
    assert rep == {"state": None, "add": []} and p.read_text() == 'model = "gpt-6"\n'


def test_perm_toml_ok(t_mod):
    assert t_mod._perm_toml_ok('a = 1\n')
    tomllib = pytest.importorskip("tomllib")
    assert not t_mod._perm_toml_ok('a = 1\na = 2\n')


def test_perm_sync_codex_carries_the_network_line(t_mod, tmp_path):
    home = tmp_path
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text('model = "gpt-6"\n')
    reps = t_mod._perm_sync(str(home), ALLOW, RETIRE, which=lambda n: None, modes=False, subagent=False)
    rep = reps["codex"]
    assert rep["state"] == "pending" and rep["network"] == "pending"
    assert rep["add"][-1] == "config.toml: [sandbox_workspace_write] network_access = true"
    assert "3 to add" in t_mod._perm_line("codex", rep) and t_mod._perm_short("codex", rep) == "codex 3 to add, 0 to retire"
    reps = t_mod._perm_sync(str(home), ALLOW, RETIRE, apply=True, which=lambda n: None, modes=False, subagent=False)
    assert reps["codex"]["state"] == "applied" and reps["codex"]["network"] == "applied"
    assert (home / ".codex" / "config.toml").read_text().startswith('model = "gpt-6"\n')
    assert "network_access = true" in (home / ".codex" / "config.toml").read_text()
    reps = t_mod._perm_sync(str(home), ALLOW, RETIRE, which=lambda n: None, modes=False, subagent=False)
    assert reps["codex"]["state"] == "synced" and reps["codex"]["network"] == "synced"
    # rules in sync, only the network line waiting → still a pending codex
    (home / ".codex" / "config.toml").write_text('model = "gpt-6"\n')
    rep = t_mod._perm_sync(str(home), ALLOW, RETIRE, which=lambda n: None, modes=False, subagent=False)["codex"]
    assert rep["state"] == "pending" and rep["add"] == ["config.toml: [sandbox_workspace_write] network_access = true"]
    # an unreadable config.toml is said, and the rules half still syncs
    (home / ".codex" / "config.toml").write_bytes(b"\xff\xfe")
    rep = t_mod._perm_sync(str(home), ALLOW, RETIRE, apply=True, which=lambda n: None, modes=False, subagent=False)["codex"]
    assert rep["state"] == "synced" and rep["network"] is None
    assert "config.toml unreadable" in t_mod._perm_line("codex", rep)


# ─── default permission mode: claude auto · codex full access ───────────────────

@pytest.mark.parametrize("data,seeds", [
    ({"permissions": {"allow": []}}, True),
    ({"permissions": {"allow": [], "defaultMode": "default"}}, False),      # a choice, even the stock one
    ({"permissions": {"allow": [], "defaultMode": "plan"}}, False),
    ({"permissions": {"allow": [], "disableAutoMode": "disable"}}, False),
    ({"permissions": {"allow": []}, "disableAutoMode": "disable"}, False),
    ({"permissions": []}, False),
    ({}, False),                                                            # _perm_json_sync makes the key first
])
def test_perm_claude_mode_plan(t_mod, data, seeds):
    assert t_mod._perm_claude_mode_plan(data) is seeds


def test_perm_json_sync_seeds_claudes_default_mode_once(t_mod, tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"model": "fable", "permissions": {"allow": list(ALLOW)}}))
    rep = t_mod._perm_json_sync(str(p), ALLOW, [], mode="auto")
    assert rep["state"] == "pending" and rep["mode"] == "pending"
    assert rep["add"] == ['settings.json: permissions.defaultMode = "auto"']
    rep = t_mod._perm_json_sync(str(p), ALLOW, [], apply=True, mode="auto")
    assert rep["state"] == "applied" and rep["mode"] == "applied"
    data = json.loads(p.read_text())
    # the pair Claude's own opt-in dialog leaves behind; everything else untouched
    assert data["permissions"]["defaultMode"] == "auto" and data["skipAutoPermissionPrompt"] is True
    assert data["model"] == "fable" and data["permissions"]["allow"] == ALLOW
    rep = t_mod._perm_json_sync(str(p), ALLOW, [], apply=True, mode="auto")
    assert rep["state"] == "synced" and rep["mode"] == "synced" and rep["add"] == []
    # switched by hand afterwards → never flipped back
    data["permissions"]["defaultMode"] = "default"
    p.write_text(json.dumps(data))
    assert t_mod._perm_json_sync(str(p), ALLOW, [], apply=True, mode="auto")["state"] == "synced"
    assert json.loads(p.read_text())["permissions"]["defaultMode"] == "default"
    # cursor's file (mode=None) never grows the key
    assert "mode" not in t_mod._perm_json_sync(str(p), ALLOW, [])


CODEX_MODE = 'approval_policy = "never"'


@pytest.mark.parametrize("have", [
    'approval_policy = "on-request"\n',
    'sandbox_mode = "workspace-write"\n',
    'default_permissions = "mine"\n',
    '[profiles.safe]\nsandbox_mode = "read-only"\n',          # a profile's counts: over-skip
    'model = "x"\n  approval_policy="never"\n',
])
def test_perm_codex_mode_plan_leaves_a_named_mode_alone(t_mod, have):
    assert t_mod._perm_codex_mode_plan(have) is None


def test_perm_codex_mode_plan_lands_above_the_first_table(t_mod):
    plan = t_mod._perm_codex_mode_plan
    out = plan("")
    assert out.startswith(CODEX_MODE) and out.endswith('sandbox_mode = "danger-full-access"\n')
    assert plan('model = "x"').startswith('model = "x"\napproval_policy')         # no trailing newline
    have = 'model = "x"\n\n# about the sandbox\n[sandbox_workspace_write]\nnetwork_access = true\n'
    out = plan(have)
    lines = out.split("\n")
    # top-level keys must precede the first table — and its comment stays on it
    assert lines.index('sandbox_mode = "danger-full-access"') < lines.index("# about the sandbox")
    assert lines.index("# about the sandbox") + 1 == lines.index("[sandbox_workspace_write]")
    assert plan(out) is None                                                       # idempotent
    tomllib = pytest.importorskip("tomllib")
    data = tomllib.loads(out)
    assert data["approval_policy"] == "never" and data["sandbox_mode"] == "danger-full-access"
    assert data["sandbox_workspace_write"] == {"network_access": True} and data["model"] == "x"
    data = tomllib.loads(plan("[a]\nb = 1\n"))                                     # a header on line 1
    assert data["approval_policy"] == "never" and data["a"] == {"b": 1}


def test_perm_codex_mode_sync(t_mod, tmp_path, monkeypatch):
    p = tmp_path / "config.toml"
    rep = t_mod._perm_codex_mode_sync(str(p))
    assert rep["state"] == "pending" and rep["add"] == [
        'config.toml: approval_policy = "never" · sandbox_mode = "danger-full-access"']
    assert t_mod._perm_codex_mode_sync(str(p), apply=True)["state"] == "applied"
    assert t_mod._perm_codex_mode_sync(str(p), apply=True) == {"state": "synced", "add": []}
    p.write_bytes(b"\xff\xfe")
    assert t_mod._perm_codex_mode_sync(str(p), apply=True) == {"state": None, "add": []}
    p.write_text('model = "x"\n')
    monkeypatch.setattr(t_mod, "_perm_toml_ok", lambda text: False)                # would not parse → untouched
    assert t_mod._perm_codex_mode_sync(str(p), apply=True) == {"state": None, "add": []}
    assert p.read_text() == 'model = "x"\n'


def test_perm_sync_seeds_both_default_modes_and_can_be_told_not_to(t_mod, tmp_path):
    home = tmp_path
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"allow": list(ALLOW)}}))
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text('model = "gpt-6"\n')
    off = t_mod._perm_sync(str(home), ALLOW, RETIRE, which=lambda n: None, modes=False, subagent=False)
    assert "mode" not in off["claude"] and "mode" not in off["codex"] and off["claude"]["state"] == "synced"
    reps = t_mod._perm_sync(str(home), ALLOW, RETIRE, apply=True, which=lambda n: None)
    assert reps["claude"]["mode"] == "applied" and reps["codex"]["mode"] == "applied"
    assert json.loads((home / ".claude" / "settings.json").read_text())["permissions"]["defaultMode"] == "auto"
    toml = (home / ".codex" / "config.toml").read_text()
    # both halves of the one file landed: the mode above the table the network line made
    assert toml.index(CODEX_MODE) < toml.index("[sandbox_workspace_write]") and toml.startswith('model = "gpt-6"\n')
    reps = t_mod._perm_sync(str(home), ALLOW, RETIRE, which=lambda n: None)
    assert {a: r["state"] for a, r in reps.items()} == {"claude": "synced", "codex": "synced", "cursor": "absent"}


def test_cmd_permissions_apply_says_which_mode_it_seeded(t_mod, tmp_path, monkeypatch, capsys):
    import argparse
    home = tmp_path
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"allow": []}}))
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text("")
    monkeypatch.setattr(t_mod, "HOME", str(home))
    monkeypatch.setattr(t_mod, "_perm_root", lambda: str(REPO_ROOT))
    monkeypatch.setenv("DOTFILES_NO_AGENT_MODES", "1")
    ns = argparse.Namespace(apply=True, show=False)
    assert t_mod.cmd_permissions(None, ns) == 0
    assert "default mode" not in capsys.readouterr().out
    assert "defaultMode" not in (home / ".claude" / "settings.json").read_text()
    monkeypatch.delenv("DOTFILES_NO_AGENT_MODES")
    assert t_mod.cmd_permissions(None, ns) == 0
    out = capsys.readouterr().out
    assert 'permissions: claude — default mode: permissions.defaultMode = "auto"' in out
    assert "permissions: codex — default mode: approval_policy" in out
    assert "added 0" not in out                  # the rules landed on the first run; only the modes now
    assert t_mod.cmd_permissions(None, ns) == 0 and capsys.readouterr().out == ""
    assert t_mod.cmd_permissions(None, argparse.Namespace(apply=False, show=True)) == 0
    assert "DOTFILES_NO_AGENT_MODES=1 opts out" in capsys.readouterr().out


def test_perm_targets_gating(t_mod, tmp_path):
    home = tmp_path
    none = lambda name: None
    t = t_mod._perm_targets(str(home), which=none)
    assert set(t) == {"claude"}                                # claude: always its settings path
    (home / ".codex").mkdir()                                  # the bare dir link_all makes: not evidence
    assert set(t_mod._perm_targets(str(home), which=none)) == {"claude"}
    (home / ".codex" / "auth.json").write_text("{}")
    assert "codex" in t_mod._perm_targets(str(home), which=none)
    (home / ".codex" / "auth.json").unlink()
    assert "codex" in t_mod._perm_targets(str(home), which=lambda n: "/usr/bin/codex" if n == "codex" else None)
    (home / ".cursor").mkdir()
    assert "cursor" not in t_mod._perm_targets(str(home), which=none)
    (home / ".cursor" / "cli-config.json").write_text("{}")
    t = t_mod._perm_targets(str(home), which=none)
    assert t["cursor"] == str(home / ".cursor" / "cli-config.json")
    assert t["claude"] == str(home / ".claude" / "settings.json")


def test_perm_sync_reports_every_agent(t_mod, tmp_path):
    home = tmp_path
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"allow": []}}))
    (home / ".cursor").mkdir()
    (home / ".cursor" / "cli-config.json").write_text(json.dumps({"permissions": {"allow": ["Shell(ls)"]}}))
    reps = t_mod._perm_sync(str(home), ALLOW, RETIRE, which=lambda n: None, modes=False, subagent=False)
    assert reps["codex"]["state"] == "absent" and reps["codex"]["path"] is None
    assert reps["claude"]["state"] == "pending" and reps["claude"]["add"] == ALLOW
    assert reps["cursor"]["state"] == "pending" and reps["cursor"]["add"] == ["Shell(gh pr)", "Shell(npx playwright)"]
    reps = t_mod._perm_sync(str(home), ALLOW, RETIRE, apply=True, which=lambda n: None, modes=False, subagent=False)
    assert {a: r["state"] for a, r in reps.items()} == {"claude": "applied", "codex": "absent", "cursor": "applied"}
    reps = t_mod._perm_sync(str(home), ALLOW, RETIRE, which=lambda n: None, modes=False, subagent=False)
    assert {a: r["state"] for a, r in reps.items()} == {"claude": "synced", "codex": "absent", "cursor": "synced"}
    assert "3 to add" not in t_mod._perm_line("claude", reps["claude"])
    assert t_mod._perm_short("codex", reps["codex"]) == "codex not installed"


def test_perm_lines_cover_every_state(t_mod):
    for st in ("absent", "missing", None, "synced", "pending", "applied"):
        rep = {"path": "/h/.claude/settings.json", "state": st, "add": ["a"], "retire": []}
        assert t_mod._perm_line("claude", rep).startswith("claude")
        assert t_mod._perm_short("claude", rep).startswith("claude")
    rep = {"path": "/h/x", "state": "pending", "add": ["a", "b"], "retire": ["c"]}
    assert "2 to add, 1 to retire" in t_mod._perm_line("cursor", rep)
    assert t_mod._perm_short("cursor", rep) == "cursor 2 to add, 1 to retire"


# ─── the shipped lists ─────────────────────────────────────────────────────────

def test_shipped_lists_are_well_formed(t_mod):
    allow, retire = t_mod._perm_lists(str(REPO_ROOT))
    assert len(allow) > 50 and retire
    assert t_mod._perm_conflicts(allow, retire) == []
    for r in allow + retire:
        assert "  " not in r and r == r.strip()
        # a public repo: no home paths, hosts or one-off command literals
        for bad in ("/Users/", "/home/", "/root/", "/tmp/", "\n"):
            assert bad not in r, r
        if r.startswith("Bash("):
            # every Bash rule is a prefix rule, so it reaches cursor too (and codex,
            # unless it carries an env assignment — those are opaque to codex's rules)
            assert t_mod._perm_bash_rule(r), r
        # the blanket mcp__sessions rule has its own seed (install_claude_mcp_allow),
        # and per-tool rules would read to it as a hand-narrowing
        assert not r.startswith("mcp__sessions"), r
        # commands and generic tool rules only: a project's MCP tool names, its skills
        # and the domains it fetched say what you work on, and the repo is public
        # (the 2026-09-14 trim — the mechanism still syncs such rules, the LIST does not carry them)
        assert r.startswith("Bash(") or r in SHIPPED_GENERIC, r
    # the broad rules the user chose to promote (2026-09-14) — pinned so a later
    # "tidy" cannot quietly drop them
    for r in ("Bash(bash:*)", "Bash(python3:*)", "Bash(node:*)", "Bash(curl:*)", "Bash(claude:*)"):
        assert r in allow
    # the env-prefixed node toolchain (2026-09-14): any variable, space spelling, so it
    # reaches claude (verified matching on 2.1.271) and cursor; never the `:*` spelling
    assert t_mod._perm_lint(allow + retire) == []
    for r in ("Bash(*=* node *)", "Bash(*=* npm run *)", "Bash(*=* npm test *)", "Bash(*=* npx vitest *)"):
        assert r in allow, r
        assert t_mod._perm_bash_rule(r)[0] == [("*", "*")]
    assert "Shell(*=* node *)" in t_mod._perm_translate(allow, "cursor")
    assert not any('"*=*"' in c for c in t_mod._perm_translate(allow, "codex"))


@pytest.mark.skipif(not shutil.which("codex"), reason="codex CLI not installed")
def test_shipped_codex_rules_validate_with_codex(t_mod, tmp_path):
    allow, _ = t_mod._perm_lists(str(REPO_ROOT))
    rules = tmp_path / "dotfiles.rules"
    rules.write_text(t_mod._perm_codex_file(allow))

    def check(*cmd):
        out = subprocess.run(["codex", "execpolicy", "check", "--rules", str(rules), "--", *cmd],
                             capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout)
    assert check("gh", "pr", "list", "--state", "open").get("decision") == "allow"
    assert check("gh", "issue", "list") == {"matchedRules": []}


def test_doctor_warns_on_pending_permission_sync(t_mod):
    facts = {"perm_sync": {"claude": {"state": "synced", "add": [], "retire": []},
                           "codex": {"state": "absent", "add": [], "retire": []},
                           "cursor": {"state": "pending", "add": ["Shell(gh pr)"], "retire": ["Shell(x)"]}}}
    out = t_mod._doctor_findings(facts)
    assert any("cursor (1 to add, 1 to retire)" in l and "t permissions --apply" in l for l in out)
    for st in ("synced", "absent", "missing", None, "applied"):
        facts["perm_sync"]["cursor"]["state"] = st
        assert t_mod._doctor_findings(facts) == ["✓ nothing suspicious found"]
    assert t_mod._doctor_findings({"perm_sync": None}) == ["✓ nothing suspicious found"]


def test_parity_matrix_has_the_permissions_row(t_mod):
    rows = [r[0] for r in t_mod._AGENT_PARITY]
    assert any(r.startswith("permissions (") for r in rows)
    assert "permissions" in t_mod.IMPLEMENTED


# ─── install.sh, links-only, end to end ────────────────────────────────────────

@pytest.fixture
def box(tmp_path):
    """(checkout, home): a seeded checkout on main carrying the REAL bin/t and the
    shipped agents/ lists, plus an empty HOME."""
    co = tmp_path / "dotfiles"
    co.mkdir()
    git("init", "-q", "-b", "main", cwd=co)
    git("config", "user.email", "t@t.t", cwd=co)
    git("config", "user.name", "T", cwd=co)
    _seed_worktree(co)
    (co / "bin" / "t").write_bytes((REPO_ROOT / "bin" / "t").read_bytes())
    (co / "agents").mkdir()
    for f in ("permissions.allow", "permissions.retire"):
        (co / "agents" / f).write_bytes((REPO_ROOT / "agents" / f).read_bytes())
    git("add", "-A", cwd=co)
    git("commit", "-qm", "seed", "--no-verify", cwd=co)
    home = tmp_path / "home"
    home.mkdir()
    return co, home


def relink(co, home, **extra):
    # pytest-cov measures SUBPROCESSES too (its .pth hook fires on the COV_CORE_* env),
    # and the sandbox's bin/t copy matches pyproject's `*/bin/t` include — so each box
    # reported as a separate, mostly-uncovered 1500-statement file and CI's gate fell
    # to 36%. The sandbox runs are exercising install.sh, not measuring bin/t.
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("COV_CORE_")},
        "HOME": str(home),
        "DOTFILES_LINKS_ONLY": "1",
        "DOTFILES_NO_TMUX": "1",
        "DOTFILES_NO_MCP": "1",
        "DOTFILES_NO_CODEX_HOOKS": "1",
        # no `codex` reachable: the gate must decide on ~/.codex alone
        "PATH": ":".join([os.path.dirname(sys.executable), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]),
    }
    env.update(extra)
    return subprocess.run(["./install.sh"], cwd=str(co), env=env, capture_output=True, text=True)


def _claude_settings(home, allow):
    (home / ".claude").mkdir(exist_ok=True)
    p = home / ".claude" / "settings.json"
    p.write_text(json.dumps({"model": "fable", "permissions": {"allow": allow, "defaultMode": "auto"}}, indent=2) + "\n")
    return p


def test_links_only_merges_into_claude_settings(t_mod, box):
    co, home = box
    p = _claude_settings(home, ["Bash(launchctl kickstart:*)", "Bash(mine:*)", "Bash(gh pr:*)"])
    r = relink(co, home)
    assert r.returncode == 0, r.stderr
    assert "permissions: claude — added" in r.stdout and "retired 1" in r.stdout
    allow, retire = t_mod._perm_lists(str(REPO_ROOT))
    data = json.loads(p.read_text())
    got = data["permissions"]["allow"]
    assert got[:2] == ["Bash(mine:*)", "Bash(gh pr:*)"]         # hand rule kept, order kept
    assert all(a in got for a in allow) and len(got) == len(set(got))
    assert "Bash(launchctl kickstart:*)" not in got               # retired
    assert data["model"] == "fable" and data["permissions"]["defaultMode"] == "auto"
    # every dots: silent, byte-identical
    before = p.read_text()
    r = relink(co, home)
    assert r.returncode == 0 and "permissions:" not in r.stdout
    assert p.read_text() == before


def test_links_only_seeds_codex_rules_for_a_real_codex_home(t_mod, box):
    co, home = box
    r = relink(co, home)
    assert r.returncode == 0, r.stderr
    # the bare ~/.codex link_all makes (prompts) is not a codex home
    assert (home / ".codex" / "prompts" / "tpush.md").is_symlink()
    assert not (home / ".codex" / "rules").exists()
    (home / ".codex" / "config.toml").write_text('model = "gpt-6"\n')
    r = relink(co, home)
    assert r.returncode == 0, r.stderr
    assert "permissions: codex — added" in r.stdout
    assert "permissions: codex — sandbox network on" in r.stdout
    allow, _ = t_mod._perm_lists(str(REPO_ROOT))
    assert (home / ".codex" / "rules" / "dotfiles.rules").read_text() == t_mod._perm_codex_file(allow)
    # the same dots turned the workspace-write sandbox's network on, add-only
    cfg = (home / ".codex" / "config.toml").read_text()
    assert cfg.startswith('model = "gpt-6"\n') and "[sandbox_workspace_write]\nnetwork_access = true" in cfg
    assert "permissions:" not in relink(co, home).stdout
    assert (home / ".codex" / "config.toml").read_text() == cfg


def test_links_only_merges_cursor_config_and_keeps_its_login(box):
    co, home = box
    (home / ".cursor").mkdir()
    p = home / ".cursor" / "cli-config.json"
    seed = {"permissions": {"allow": ["Shell(ls)"], "deny": []}, "version": 1,
            "authInfo": {"email": "x@y", "userId": 1}, "approvalMode": "allowlist"}
    p.write_text(json.dumps(seed, indent=2) + "\n")
    r = relink(co, home)
    assert r.returncode == 0, r.stderr
    assert "permissions: cursor — added" in r.stdout
    data = json.loads(p.read_text())
    got = data["permissions"]["allow"]
    assert got[0] == "Shell(ls)" and got.count("Shell(ls)") == 1
    assert "Shell(gh pr)" in got and "Shell(bash)" in got
    assert not any("WebFetch" in g or "mcp__" in g for g in got)   # claude-only rules stay claude-only
    for k in ("version", "authInfo", "approvalMode"):
        assert data[k] == seed[k]
    assert data["permissions"]["deny"] == []


def test_no_settings_file_is_a_noop_in_links_only(box):
    co, home = box
    r = relink(co, home)
    assert r.returncode == 0, r.stderr
    assert not (home / ".claude" / "settings.json").exists()     # the full install seeds it, then merges
    assert "permissions:" not in r.stdout


def test_a_stub_bin_t_is_a_silent_noop(box):
    co, home = box
    p = _claude_settings(home, ["Bash(mine:*)"])
    (co / "bin" / "t").write_text("#!/bin/sh\n")
    r = relink(co, home)
    assert r.returncode == 0, r.stderr
    assert "permissions" not in r.stdout + r.stderr
    assert json.loads(p.read_text())["permissions"]["allow"] == ["Bash(mine:*)"]


def test_opt_out_env(box):
    co, home = box
    p = _claude_settings(home, ["Bash(mine:*)"])
    r = relink(co, home, DOTFILES_NO_PERMISSIONS="1")
    assert r.returncode == 0, r.stderr
    assert json.loads(p.read_text())["permissions"]["allow"] == ["Bash(mine:*)"]


def test_install_sh_runs_it_in_the_links_only_path_and_after_the_settings_seed():
    sh = (REPO_ROOT / "install.sh").read_text()
    exit_at = sh.index('if [[ -n "${DOTFILES_LINKS_ONLY:-}" ]]; then\n    exit 0')
    first = sh.index("install_agent_permissions\n")
    assert first < exit_at, "a plain dots must reach the permission sync"
    seed = sh.index("\ninstall_claude_settings\n")            # the call, not a comment mention
    assert seed > exit_at
    assert sh.index("\ninstall_agent_permissions\n", seed) > seed, \
        "a fresh box's just-seeded settings.json gets the list in the same run"


def test_perm_root_is_the_checkout_holding_bin_t(t_mod):
    assert pathlib.Path(t_mod._perm_root()) == REPO_ROOT
    allow, retire = t_mod._perm_lists(t_mod._perm_root())
    assert allow and retire


# ─── the default subagent model ────────────────────────────────────────────────

CLAUDE_SUB = ("CLAUDE_CODE_SUBAGENT_MODEL", "sonnet")


@pytest.mark.parametrize("data, seed", [
    ({}, True),                                                    # no env block at all
    ({"env": {"FOO": "1"}}, True),                                 # a block without the key
    ({"env": {"CLAUDE_CODE_SUBAGENT_MODEL": "haiku"}}, False),     # a hand-picked model
    ({"env": {"CLAUDE_CODE_SUBAGENT_MODEL": "inherit"}}, False),   # "inherit" is a choice too
    ({"env": {"CLAUDE_CODE_SUBAGENT_MODEL": ""}}, False),
    ({"env": ["not", "an", "object"]}, False),                     # foreign-shaped: left alone
])
def test_perm_claude_subagent_plan(t_mod, data, seed):
    assert t_mod._perm_claude_subagent_plan(data, CLAUDE_SUB) is seed


def test_perm_json_sync_seeds_the_claude_subagent_model_once(t_mod, tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"permissions": {"allow": list(ALLOW)}, "env": {"FOO": "1"}, "model": "opus"}))
    rep = t_mod._perm_json_sync(str(p), ALLOW, [], subagent=CLAUDE_SUB)
    assert rep["state"] == "pending" and rep["subagent"] == "pending"
    assert rep["add"] == ['settings.json: env.CLAUDE_CODE_SUBAGENT_MODEL = "sonnet"']
    rep = t_mod._perm_json_sync(str(p), ALLOW, [], apply=True, subagent=CLAUDE_SUB)
    assert rep["subagent"] == "applied"
    data = json.loads(p.read_text())
    assert data["env"] == {"FOO": "1", "CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"} and data["model"] == "opus"
    assert t_mod._perm_json_sync(str(p), ALLOW, [], apply=True, subagent=CLAUDE_SUB)["subagent"] == "synced"
    # a hand change is never flipped back
    data["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] = "inherit"
    p.write_text(json.dumps(data))
    rep = t_mod._perm_json_sync(str(p), ALLOW, [], apply=True, subagent=CLAUDE_SUB)
    assert rep["state"] == "synced" and json.loads(p.read_text())["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] == "inherit"
    # without subagent= (cursor) the env block is never grown
    q = tmp_path / "cli-config.json"
    q.write_text(json.dumps({"permissions": {"allow": []}}))
    t_mod._perm_json_sync(str(q), ["Shell(ls)"], [], apply=True)
    assert "env" not in json.loads(q.read_text())


@pytest.mark.parametrize("have, expect", [
    ("", "append"),
    ('model = "gpt-6-astra"\n', "append"),
    ('model = "x"\n\n[agents]\nmax_depth = 2\n', "insert"),
    ('[agents]\ndefault_subagent_model = "gpt-6-luna"\n', None),   # a hand-picked model
    ('agents.default_subagent_model = "gpt-6-luna"\n', None),      # dotted
    ('agents = { max_depth = 2 }\n', None),                        # inline table: cannot extend
])
def test_perm_codex_subagent_plan(t_mod, have, expect):
    out = t_mod._perm_codex_subagent_plan(have)
    if expect is None:
        assert out is None
        return
    tomllib = pytest.importorskip("tomllib")
    data = tomllib.loads(out)
    assert data["agents"]["default_subagent_model"] == "gpt-6-sol"
    if expect == "insert":
        assert data["agents"]["max_depth"] == 2 and out.count("[agents]") == 1
    else:
        assert out.startswith(have.rstrip("\n"))


def test_perm_sync_seeds_both_subagent_models_and_can_be_told_not_to(t_mod, tmp_path):
    home = tmp_path
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"allow": list(ALLOW)}}))
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text('model = "gpt-6-astra"\n')
    off = t_mod._perm_sync(str(home), ALLOW, RETIRE, which=lambda n: None, modes=False, subagent=False)
    assert "subagent" not in off["claude"] and "subagent" not in off["codex"]
    reps = t_mod._perm_sync(str(home), ALLOW, RETIRE, apply=True, which=lambda n: None, modes=False)
    assert reps["claude"]["subagent"] == "applied" and reps["codex"]["subagent"] == "applied"
    assert json.loads((home / ".claude" / "settings.json").read_text())["env"] == {"CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"}
    assert '[agents]\ndefault_subagent_model = "gpt-6-sol"' in (home / ".codex" / "config.toml").read_text()
    reps = t_mod._perm_sync(str(home), ALLOW, RETIRE, which=lambda n: None, modes=False)
    assert {a: r["state"] for a, r in reps.items()} == {"claude": "synced", "codex": "synced", "cursor": "absent"}


def test_cmd_permissions_apply_says_which_subagent_model_it_seeded(t_mod, tmp_path, monkeypatch, capsys):
    import argparse
    home = tmp_path
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"allow": []}}))
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text("")
    monkeypatch.setattr(t_mod, "HOME", str(home))
    monkeypatch.setattr(t_mod, "_perm_root", lambda: str(REPO_ROOT))
    monkeypatch.setenv("DOTFILES_NO_AGENT_MODES", "1")
    monkeypatch.setenv("DOTFILES_NO_SUBAGENT_MODEL", "1")
    ns = argparse.Namespace(apply=True, show=False)
    assert t_mod.cmd_permissions(None, ns) == 0
    assert "subagent" not in capsys.readouterr().out
    monkeypatch.delenv("DOTFILES_NO_SUBAGENT_MODEL")
    assert t_mod.cmd_permissions(None, ns) == 0
    out = capsys.readouterr().out
    assert 'permissions: claude — default subagent model: env.CLAUDE_CODE_SUBAGENT_MODEL = "sonnet"' in out
    assert 'permissions: codex — default subagent model: [agents] default_subagent_model = "gpt-6-sol"' in out
    assert "added 0" not in out
    assert t_mod.cmd_permissions(None, ns) == 0 and capsys.readouterr().out == ""
    assert t_mod.cmd_permissions(None, argparse.Namespace(apply=False, show=True)) == 0
    assert "DOTFILES_NO_SUBAGENT_MODEL=1 opts out" in capsys.readouterr().out
