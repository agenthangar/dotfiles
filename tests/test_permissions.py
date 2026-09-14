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


def test_perm_translate_per_agent(t_mod):
    rules = ["Bash(awk *)", "Bash(awk:*)", "Bash(gh pr:*)", "Bash(xxd)", "WebSearch", "mcp__x__y"]
    assert t_mod._perm_translate(rules, "claude") == rules
    assert t_mod._perm_translate(rules, "codex") == [
        'prefix_rule(pattern=["awk"], decision="allow")',
        'prefix_rule(pattern=["gh", "pr"], decision="allow")']
    assert t_mod._perm_translate(rules, "cursor") == ["Shell(awk)", "Shell(gh pr)"]


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
    reps = t_mod._perm_sync(str(home), ALLOW, RETIRE, which=lambda n: None)
    assert reps["codex"]["state"] == "absent" and reps["codex"]["path"] is None
    assert reps["claude"]["state"] == "pending" and reps["claude"]["add"] == ALLOW
    assert reps["cursor"]["state"] == "pending" and reps["cursor"]["add"] == ["Shell(gh pr)", "Shell(npx playwright)"]
    reps = t_mod._perm_sync(str(home), ALLOW, RETIRE, apply=True, which=lambda n: None)
    assert {a: r["state"] for a, r in reps.items()} == {"claude": "applied", "codex": "absent", "cursor": "applied"}
    reps = t_mod._perm_sync(str(home), ALLOW, RETIRE, which=lambda n: None)
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
            # every Bash rule is a prefix rule, so it reaches codex and cursor too
            assert t_mod._perm_bash_prefix(r), r
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
    env = {
        **os.environ,
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
    allow, _ = t_mod._perm_lists(str(REPO_ROOT))
    assert (home / ".codex" / "rules" / "dotfiles.rules").read_text() == t_mod._perm_codex_file(allow)
    assert "permissions:" not in relink(co, home).stdout


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
