"""`t trust` — folder trust in every installed agent's own store (bin/t), and the
install.sh step that trusts every registered repo on each `dots`. Covers the pure
path resolution (canonical repo behind a linked worktree, the too-wide guard), each
agent's planner + sync over a sandbox HOME, Claude's config lock, the verb itself, the
doctor finding, the plan steps `t setup` / `t new` grew, and install.sh links-only end
to end with the REAL bin/t (the permissions-test shape)."""

import argparse
import json
import os
import pathlib
import stat
import time

import pytest

from test_install_migration import git
from test_permissions import box, relink  # noqa: F401  (box is a fixture)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
NONE = lambda name: None   # noqa: E731  — no agent binary on PATH


def _repo(path):
    path.mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=path)
    git("config", "user.email", "t@t.t", cwd=path)
    git("config", "user.name", "T", cwd=path)
    git("commit", "-q", "--allow-empty", "-m", "init", "--no-verify", cwd=path)
    return path


def _home(tmp_path, claude=True, codex=True, cursor=True):
    """A HOME where each agent has left the file its first run writes."""
    home = tmp_path / "home"
    home.mkdir()
    if claude:
        p = home / ".claude.json"
        p.write_text(json.dumps({"numStartups": 7, "oauthAccount": {"k": "secret"},
                                 "projects": {"/elsewhere": {"allowedTools": ["x"]}}}, indent=2))
        p.chmod(0o600)
    if codex:
        (home / ".codex").mkdir()
        (home / ".codex" / "config.toml").write_text('model = "gpt-6"\n')
    if cursor:
        (home / ".cursor").mkdir()
        (home / ".cursor" / "cli-config.json").write_text(json.dumps({"authInfo": {"t": "login"}}))
    return home


# ─── which directory an agent keys trust on ─────────────────────────────────────

def test_git_roots_plain_repo_subdir_and_outside(t_mod, tmp_path):
    repo = _repo(tmp_path / "code" / "api")
    (repo / "src" / "deep").mkdir(parents=True)
    assert t_mod._trust_git_roots(str(repo)) == (str(repo), str(repo))
    assert t_mod._trust_git_roots(str(repo / "src" / "deep")) == (str(repo), str(repo))
    plain = tmp_path / "notes"
    plain.mkdir()
    assert t_mod._trust_git_roots(str(plain)) == (None, None)


def test_git_roots_linked_worktree_resolves_to_its_canonical_repo(t_mod, tmp_path):
    # THE case the verb exists for: claude and codex both look trust up on the main
    # checkout, so one entry covers every per-session worktree
    repo = _repo(tmp_path / "code" / "api")
    wt = tmp_path / "code" / ".worktrees" / "api" / "3"
    git("worktree", "add", "-q", "-b", "dev/api-3", str(wt), cwd=repo)
    tree, canon = t_mod._trust_git_roots(str(wt))
    assert tree == str(wt)
    assert os.path.realpath(canon) == os.path.realpath(str(repo))


def test_canonical_leaves_other_gitfiles_alone(t_mod, tmp_path):
    sub = tmp_path / "super" / "vendor" / "lib"
    sub.mkdir(parents=True)
    (sub / ".git").write_text("gitdir: ../../.git/modules/lib\n")     # a submodule
    assert t_mod._trust_git_roots(str(sub)) == (str(sub), str(sub))
    (sub / ".git").write_text("not a gitfile\n")
    assert t_mod._trust_canonical(str(sub), str(sub / ".git")) == str(sub)
    assert t_mod._trust_canonical(str(sub), str(sub / "missing")) == str(sub)
    bare = tmp_path / "wt"
    bare.mkdir()
    (bare / ".git").write_text("gitdir: /srv/repo.git/worktrees/wt\n")  # a bare repo's worktree
    assert t_mod._trust_canonical(str(bare), str(bare / ".git")) == str(bare)


@pytest.mark.parametrize("path,wide", [
    ("/Users/me", True), ("/Users", True), ("/", True),
    ("/opt", True), ("/opt/x", True),                  # under 3 segments: cursor ignores it
    ("/Users/me/code", False), ("/Users/me/code/api", False), ("/srv/www/site", False),
])
def test_too_wide(t_mod, path, wide):
    assert t_mod._trust_too_wide(path, "/Users/me") is wide


def test_records_dedupe_and_mark_cursor_only_trees(t_mod, tmp_path):
    repo = _repo(tmp_path / "code" / "api")
    wt = tmp_path / "code" / ".worktrees" / "api" / "1"
    git("worktree", "add", "-q", "-b", "dev/api-1", str(wt), cwd=repo)
    root = tmp_path / "code" / ".worktrees"
    recs = t_mod._trust_records([str(repo), str(repo / "."), str(wt)], [str(root)])
    # `repo/.` is the same checkout (deduped); the worktree shares its repo, not its tree
    real = os.path.realpath
    assert [(real(r["repo"]), real(r["tree"]), r["cursor_only"]) for r in recs] == [
        (real(str(repo)), real(str(repo)), False),
        (real(str(repo)), real(str(wt)), False),
        (real(str(root)), real(str(root)), True)]
    # claude/codex: the canonical repo only; cursor: + the linked tree + the root
    canon = {os.path.realpath(p) for p in t_mod._trust_agent_paths(recs, "claude")}
    assert canon == {os.path.realpath(str(repo))}
    assert {os.path.realpath(p) for p in t_mod._trust_agent_paths(recs, "codex")} == canon
    cur = {os.path.realpath(p) for p in t_mod._trust_agent_paths(recs, "cursor")}
    assert cur == {os.path.realpath(str(repo)), os.path.realpath(str(wt)), os.path.realpath(str(root))}


def test_spellings_adds_the_realpath_when_it_differs(t_mod, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    assert t_mod._trust_spellings(str(real.resolve())) == [str(real.resolve())]
    assert t_mod._trust_spellings(str(link)) == [str(link), str(real.resolve())]


# ─── claude: ~/.claude.json ─────────────────────────────────────────────────────

def test_claude_plan(t_mod):
    data = {"projects": {"/a": {"hasTrustDialogAccepted": True}, "/b": {"hasTrustDialogAccepted": False}}}
    assert t_mod._trust_claude_plan(data, ["/a", "/b", "/c"]) == ["/b", "/c"]
    assert t_mod._trust_claude_plan({}, ["/a"]) == ["/a"]                 # no projects key yet
    assert t_mod._trust_claude_plan({"projects": []}, ["/a"]) is None      # foreign shape
    assert t_mod._trust_claude_plan({"projects": {"/a": "x"}}, ["/a"]) is None
    assert t_mod._trust_claude_plan([], ["/a"]) is None


def test_claude_sync_sets_only_the_trust_key_and_keeps_the_file_private(t_mod, tmp_path):
    home = _home(tmp_path)
    p = home / ".claude.json"
    rep = t_mod._trust_claude_sync(str(p), ["/code/api"])
    assert rep == {"state": "pending", "add": ["/code/api"]}
    assert "/code/api" not in p.read_text()                               # status only
    rep = t_mod._trust_claude_sync(str(p), ["/code/api", "/elsewhere"], apply=True)
    assert rep["state"] == "applied" and rep["add"] == ["/code/api", "/elsewhere"]
    data = json.loads(p.read_text())
    assert data["projects"]["/code/api"] == {"hasTrustDialogAccepted": True}
    # an existing project keeps everything it had; the login and the rest survive
    assert data["projects"]["/elsewhere"] == {"allowedTools": ["x"], "hasTrustDialogAccepted": True}
    assert data["oauthAccount"] == {"k": "secret"} and data["numStartups"] == 7
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert not (home / ".claude.json.lock").exists()                      # lock released
    assert t_mod._trust_claude_sync(str(p), ["/code/api"], apply=True) == {"state": "synced", "add": []}


def test_claude_sync_missing_and_unreadable(t_mod, tmp_path):
    p = tmp_path / ".claude.json"
    assert t_mod._trust_claude_sync(str(p), ["/a"], apply=True)["state"] == "missing"
    assert not p.exists()                     # never created: it carries the login
    p.write_text("{not json")
    assert t_mod._trust_claude_sync(str(p), ["/a"], apply=True) == {"state": None, "add": []}
    assert p.read_text() == "{not json"


def test_claude_sync_backs_off_a_live_lock_and_ignores_a_dead_one(t_mod, tmp_path, monkeypatch):
    home = _home(tmp_path)
    p = home / ".claude.json"
    lock = home / ".claude.json.lock"
    lock.mkdir()                               # a running claude is mid read-modify-write
    before = p.read_text()
    monkeypatch.setattr(t_mod.time, "sleep", lambda s: None)
    real_lock = t_mod._trust_lock
    monkeypatch.setattr(t_mod, "_trust_lock", lambda path: real_lock(path, wait=0.0))
    rep = t_mod._trust_claude_sync(str(p), ["/a"], apply=True)
    assert rep["state"] == "busy" and p.read_text() == before
    assert "re-run" in t_mod._trust_line("claude", {**rep, "path": str(p), "want": 1})
    # a lock nobody has refreshed for a minute: its holder is gone — proceed, leave it be
    old = time.time() - 120
    os.utime(lock, (old, old))
    rep = t_mod._trust_claude_sync(str(p), ["/a"], apply=True)
    assert rep["state"] == "applied" and lock.is_dir()


def test_trust_lock_states(t_mod, tmp_path):
    lock = str(tmp_path / "f.lock")
    assert t_mod._trust_lock(lock) is True and os.path.isdir(lock)
    assert t_mod._trust_lock(lock, wait=0.0) is None
    assert t_mod._trust_lock(str(tmp_path / "no" / "such" / "dir.lock")) is False


def test_trust_lock_waits_for_a_live_holder_to_finish(t_mod, tmp_path, monkeypatch):
    lock = tmp_path / "f.lock"
    lock.mkdir()
    monkeypatch.setattr(t_mod.time, "sleep", lambda s: lock.rmdir())    # claude lets go while we wait
    assert t_mod._trust_lock(str(lock)) is True and lock.is_dir()


def test_trust_lock_survives_the_lock_vanishing_mid_check(t_mod, tmp_path, monkeypatch):
    # claude released it between our mkdir and our stat: just try again
    lock, real_mkdir, calls = str(tmp_path / "f.lock"), os.mkdir, []

    def mkdir(path, *a):
        calls.append(path)
        if len(calls) == 1:
            raise FileExistsError(path)
        return real_mkdir(path, *a)
    monkeypatch.setattr(t_mod.os, "mkdir", mkdir)
    assert t_mod._trust_lock(lock) is True and len(calls) == 2


def test_claude_sync_rereads_under_the_lock(t_mod, tmp_path, monkeypatch):
    home = _home(tmp_path)
    p = home / ".claude.json"

    def claude_wrote_meanwhile(lock):
        data = json.loads(p.read_text())
        data["numStartups"] = 8
        data["projects"]["/a"] = {"hasTrustDialogAccepted": True}       # …and trusted /a itself
        p.write_text(json.dumps(data))
        return False
    monkeypatch.setattr(t_mod, "_trust_lock", claude_wrote_meanwhile)
    rep = t_mod._trust_claude_sync(str(p), ["/a", "/b"], apply=True)
    assert rep == {"state": "applied", "add": ["/b"]}                   # planned from the FRESH read
    assert json.loads(p.read_text())["numStartups"] == 8                # claude's write survived ours

    def claude_broke_it(lock):
        p.write_text("{half a write")
        return True                                                     # "held", but no dir: rmdir fails quietly
    monkeypatch.setattr(t_mod, "_trust_lock", claude_broke_it)
    p.write_text(json.dumps({"projects": {}}))
    assert t_mod._trust_claude_sync(str(p), ["/a"], apply=True) == {"state": None, "add": []}
    assert p.read_text() == "{half a write"


def test_claude_sync_a_failed_write_leaves_the_file_and_the_lock_clean(t_mod, tmp_path, monkeypatch):
    home = _home(tmp_path)
    p = home / ".claude.json"
    before = p.read_text()

    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(t_mod, "_trust_write_private", boom)
    assert t_mod._trust_claude_sync(str(p), ["/a"], apply=True) == {"state": None, "add": []}
    assert p.read_text() == before and not (home / ".claude.json.lock").exists()


# ─── codex: ~/.codex/config.toml ────────────────────────────────────────────────

CODEX_HAVE = ('model = "gpt-6"\n\n[projects."/code/api"]\ntrust_level = "trusted"\n\n'
              "[projects.'/code/lit']\ntrust_level = \"untrusted\"\n")


def test_codex_plan_appends_only_unknown_projects(t_mod):
    want, added = t_mod._trust_codex_plan(CODEX_HAVE, ["/code/api", "/code/lit", "/code/new"])
    assert added == ["/code/new"]
    assert want == CODEX_HAVE + '\n[projects."/code/new"]\ntrust_level = "trusted"\n'
    # an explicit `untrusted` is a decision — never flipped
    assert "[projects.'/code/lit']\ntrust_level = \"untrusted\"" in want
    assert t_mod._trust_codex_plan(CODEX_HAVE, ["/code/api"]) == (CODEX_HAVE, [])


def test_codex_plan_empty_file_no_trailing_newline_and_quoting(t_mod):
    want, added = t_mod._trust_codex_plan("", ["/a"])
    assert want == '[projects."/a"]\ntrust_level = "trusted"\n' and added == ["/a"]
    want, _ = t_mod._trust_codex_plan('model = "x"', ["/a"])
    assert want == 'model = "x"\n\n[projects."/a"]\ntrust_level = "trusted"\n'
    odd = '/code/we"ird\\dir'
    want, _ = t_mod._trust_codex_plan("", [odd])
    assert '[projects."/code/we\\"ird\\\\dir"]' in want
    assert t_mod._trust_codex_plan(want, [odd]) == (want, [])             # round-trips
    tomllib = pytest.importorskip("tomllib")
    assert tomllib.loads(want)["projects"][odd]["trust_level"] == "trusted"


@pytest.mark.parametrize("text", [
    'projects = { "/a" = { trust_level = "trusted" } }\n',               # older codex: inline
    'projects."/a".trust_level = "trusted"\n',                           # dotted keys
])
def test_codex_plan_leaves_a_projects_spelling_it_cannot_extend(t_mod, text):
    assert t_mod._trust_codex_plan(text, ["/b"]) == (None, [])


def test_codex_plan_reads_a_header_it_cannot_unescape_literally(t_mod):
    text = '[projects."/code/\\q"]\ntrust_level = "trusted"\n'          # \q: not a JSON escape
    assert t_mod._trust_codex_plan(text, ["/code/\\q"]) == (text, [])


def test_codex_sync(t_mod, tmp_path):
    p = tmp_path / "config.toml"
    assert t_mod._trust_codex_sync(str(p), ["/a"]) == {"state": "pending", "add": ["/a"]}
    assert not p.exists()
    assert t_mod._trust_codex_sync(str(p), ["/a"], apply=True)["state"] == "applied"
    assert t_mod._trust_codex_sync(str(p), ["/a"], apply=True) == {"state": "synced", "add": []}
    p.write_text('projects = { "/z" = { trust_level = "trusted" } }\n')
    assert t_mod._trust_codex_sync(str(p), ["/a"], apply=True) == {"state": None, "add": []}
    p.write_bytes(b"\xff\xfe")
    assert t_mod._trust_codex_sync(str(p), ["/a"], apply=True) == {"state": None, "add": []}


# ─── cursor: ~/.cursor/projects/<slug>/.workspace-trusted ───────────────────────

def test_cursor_slug_is_cursor_agents_own(t_mod):
    # its workspace-paths.js: replace(/[^a-zA-Z0-9]/g,"-").replace(/-+/g,"-").replace(/^-+|-+$/g,"")
    assert t_mod._trust_cursor_slug("/Users/me/code/my_repo.v2") == "Users-me-code-my-repo-v2"
    assert t_mod._trust_cursor_slug("/Users/me/code/.worktrees/api/3") == "Users-me-code-worktrees-api-3"


def test_cursor_sync_writes_markers_and_honours_a_trusted_parent(t_mod, tmp_path):
    proj, home = tmp_path / "projects", "/Users/me"
    paths = ["/Users/me/code/api", "/Users/me/code/.worktrees"]
    rep = t_mod._trust_cursor_sync(str(proj), paths, home)
    assert rep == {"state": "pending", "add": paths} and not proj.exists()
    assert t_mod._trust_cursor_sync(str(proj), paths, home, apply=True)["state"] == "applied"
    mark = json.loads((proj / "Users-me-code-api" / ".workspace-trusted").read_text())
    assert mark["workspacePath"] == "/Users/me/code/api" and mark["trustMethod"] == "t-trust"
    assert t_mod._trust_cursor_sync(str(proj), paths, home) == {"state": "synced", "add": []}
    # a session worktree is covered by the root's marker — cursor walks up through parents
    assert t_mod._trust_cursor_covered(str(proj), "/Users/me/code/.worktrees/api/7", home)
    assert not t_mod._trust_cursor_covered(str(proj), "/Users/me/other/x", home)
    # …but never by a marker on $HOME or above: cursor ignores those, so must we
    (proj / "Users-me").mkdir()
    (proj / "Users-me" / ".workspace-trusted").write_text("{}")
    assert not t_mod._trust_cursor_covered(str(proj), "/Users/me/other/x", home)
    assert t_mod._trust_cursor_covered(str(proj), "/Users/me", home)      # its own marker counts


def test_cursor_sync_unwritable_projects_dir_is_left_alone(t_mod, tmp_path):
    proj = tmp_path / "projects"
    proj.write_text("a file where the dir should be")
    assert t_mod._trust_cursor_sync(str(proj), ["/Users/me/code/api"], "/Users/me", apply=True) == {"state": None, "add": []}


# ─── the per-machine sync + the verb ────────────────────────────────────────────

def test_targets_gate_on_what_each_agents_first_run_writes(t_mod, tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / ".codex").mkdir()                   # the dir link_all makes: not evidence
    assert t_mod._trust_targets(str(bare), which=NONE) == {}
    assert set(t_mod._trust_targets(str(bare), which=lambda n: "/bin/codex")) == {"codex"}
    home = _home(tmp_path)
    assert set(t_mod._trust_targets(str(home), which=NONE)) == {"claude", "codex", "cursor"}


def test_sync_reports_every_agent_and_absent_ones(t_mod, tmp_path):
    home = _home(tmp_path, cursor=False)
    repo = _repo(tmp_path / "code" / "api")
    recs = t_mod._trust_records([str(repo)])
    reps = t_mod._trust_sync(str(home), recs, which=NONE)
    assert {a: r["state"] for a, r in reps.items()} == {"claude": "pending", "codex": "pending", "cursor": "absent"}
    assert t_mod._trust_short("claude", reps["claude"]).endswith("0/%d" % reps["claude"]["want"])
    reps = t_mod._trust_sync(str(home), recs, apply=True, which=NONE)
    assert reps["claude"]["state"] == reps["codex"]["state"] == "applied"
    reps = t_mod._trust_sync(str(home), recs, which=NONE)
    assert reps["claude"]["state"] == reps["codex"]["state"] == "synced"
    n = reps["claude"]["want"]
    assert t_mod._trust_short("claude", reps["claude"]) == "claude %d/%d" % (n, n)
    assert t_mod._trust_short("cursor", reps["cursor"]) == "cursor not installed"


def test_lines_cover_every_state(t_mod):
    for st in ("absent", "missing", "busy", None, "synced", "pending", "applied"):
        rep = {"path": "/h/.claude.json", "state": st, "add": ["/code/api"], "want": 2}
        assert "claude" in t_mod._trust_line("claude", rep)
        assert t_mod._trust_short("claude", rep).startswith("claude")
        # quiet (the dots path) says only what it changed
        assert (t_mod._trust_line("claude", rep, quiet=True) is not None) == (st == "applied")
    assert "NOT trusted: /code/api" in t_mod._trust_line("codex", {"path": "/x", "state": "pending", "add": ["/code/api"], "want": 1})
    # the already-trusted repos are NAMED next to the missing ones — a line that listed
    # only the missing read as "the repos being trusted", with dotfiles apparently left out
    rep = {"path": "/x", "state": "pending", "add": ["/code/new"], "have": ["/code/dotfiles", "/code/ff"], "want": 3}
    assert t_mod._trust_line("codex", rep) == "⬡ codex   NOT trusted: /code/new — t trust · already: /code/dotfiles, /code/ff"
    rep = {"path": "/x", "state": "synced", "add": [], "have": ["/code/dotfiles"], "want": 1}
    assert t_mod._trust_line("claude", rep) == "✱ claude  already trusted: /code/dotfiles"
    rep = {"path": "/x", "state": "applied", "add": ["/code/new"], "have": ["/code/dotfiles"], "want": 2}
    assert t_mod._trust_line("claude", rep, quiet=True) == "✱ claude  trusted /code/new · already: /code/dotfiles"


def test_sync_reports_what_was_already_trusted(t_mod, tmp_path):
    home = _home(tmp_path, cursor=False)
    old, new = _repo(tmp_path / "code" / "dotfiles"), _repo(tmp_path / "code" / "new")
    t_mod._trust_sync(str(home), t_mod._trust_records([str(old)]), apply=True, which=NONE)
    reps = t_mod._trust_sync(str(home), t_mod._trust_records([str(old), str(new)]), which=NONE)
    for a in ("claude", "codex"):
        assert reps[a]["state"] == "pending"
        assert [os.path.realpath(p) for p in reps[a]["add"]] == [os.path.realpath(str(new))]
        assert [os.path.realpath(p) for p in reps[a]["have"]] == [os.path.realpath(str(old))]
        assert "dotfiles" in t_mod._trust_line(a, reps[a]) and "new" in t_mod._trust_line(a, reps[a])
    assert reps["cursor"] == {"state": "absent", "add": [], "have": [], "path": None, "want": 2}
    reps = t_mod._trust_sync(str(home), t_mod._trust_records([str(old), str(new)]), apply=True, which=NONE)
    assert len(reps["claude"]["have"]) == 1 and reps["claude"]["state"] == "applied"


def _args(**kw):
    return argparse.Namespace(**{"dirs": [], "all": False, "status": False, "quiet": False, **kw})


def test_cmd_trust_bare_trusts_the_repo_you_are_in(t_mod, tmp_path, monkeypatch, capsys):
    home = _home(tmp_path)
    repo = _repo(tmp_path / "code" / "api")
    wt = tmp_path / "code" / ".worktrees" / "api" / "2"
    git("worktree", "add", "-q", "-b", "dev/api-2", str(wt), cwd=repo)
    monkeypatch.setattr(t_mod, "HOME", str(home))
    monkeypatch.setattr(t_mod.shutil, "which", NONE)
    monkeypatch.chdir(wt)                       # standing in a session worktree
    cfg = argparse.Namespace(repos={}, worktree_root=str(tmp_path / "code" / ".worktrees"))
    assert t_mod.cmd_trust(cfg, _args(status=True)) == 0
    assert "NOT trusted" in capsys.readouterr().out
    assert "projects" in json.loads((home / ".claude.json").read_text())   # untouched by --status
    assert t_mod.cmd_trust(cfg, _args()) == 0
    out = capsys.readouterr().out
    assert out.count("trusted ") >= 3 and "NOT trusted" not in out
    keys = {os.path.realpath(k) for k, v in json.loads((home / ".claude.json").read_text())["projects"].items()
            if v.get("hasTrustDialogAccepted")}
    assert keys == {os.path.realpath(str(repo))}                           # the CANONICAL repo, not the worktree
    toml = (home / ".codex" / "config.toml").read_text()
    assert "api\"]\ntrust_level = \"trusted\"" in toml and ".worktrees" not in toml
    # cursor needs the worktree too (it is not under its canonical repo)
    slugs = {p.name for p in (home / ".cursor" / "projects").iterdir()}
    assert any(s.endswith("worktrees-api-2") for s in slugs) and any(s.endswith("code-api") for s in slugs)


def test_cmd_trust_all_quiet_is_silent_once_synced(t_mod, tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, cursor=False)
    a, b = _repo(tmp_path / "code" / "a"), _repo(tmp_path / "code" / "b")
    monkeypatch.setattr(t_mod, "HOME", str(home))
    monkeypatch.setattr(t_mod.shutil, "which", NONE)
    cfg = argparse.Namespace(repos={"a": str(a), "aa": str(a), "b": str(b), "gone": str(tmp_path / "nope")},
                             worktree_root=str(tmp_path / "code" / ".worktrees"))
    assert t_mod._trust_all_dirs(cfg) == ([str(a), str(b)], [])            # deduped, existing only
    assert t_mod.cmd_trust(cfg, _args(all=True, quiet=True)) == 0
    out = capsys.readouterr().out
    assert out.count("trust: ") == 2 and "claude" in out and "codex" in out and "cursor" not in out
    assert t_mod.cmd_trust(cfg, _args(all=True, quiet=True)) == 0
    assert capsys.readouterr().out == ""
    assert t_mod.cmd_trust(cfg, _args(all=True)) == 0
    assert "2 registered repo(s)" in capsys.readouterr().out


def test_cmd_trust_refusals(t_mod, tmp_path, monkeypatch, capsys):
    home = _home(tmp_path)
    monkeypatch.setattr(t_mod, "HOME", str(home))
    cfg = argparse.Namespace(repos={}, worktree_root=str(tmp_path / "wt"))
    assert t_mod.cmd_trust(cfg, _args(dirs=[str(home)])) == 1              # your whole home
    assert "refusing" in capsys.readouterr().err
    assert t_mod.cmd_trust(cfg, _args(dirs=[str(tmp_path / "nope")])) == 1
    assert "not a directory" in capsys.readouterr().err
    assert t_mod.cmd_trust(cfg, _args(all=True)) == 1                       # nothing registered
    assert "t setup" in capsys.readouterr().err
    assert t_mod.cmd_trust(cfg, _args(all=True, quiet=True)) == 0           # …which dots must survive
    assert json.loads((home / ".claude.json").read_text())["projects"] == {"/elsewhere": {"allowedTools": ["x"]}}


def test_trust_auto_and_step_honour_the_opt_out(t_mod, tmp_path, monkeypatch):
    home = _home(tmp_path, cursor=False)
    repo = _repo(tmp_path / "code" / "api")
    monkeypatch.setattr(t_mod, "HOME", str(home))
    monkeypatch.setattr(t_mod.shutil, "which", NONE)
    monkeypatch.setenv("DOTFILES_NO_TRUST", "1")
    assert t_mod._trust_step([str(repo)]) is None
    t_mod._trust_auto([str(repo)])
    assert "api" not in (home / ".claude.json").read_text()
    monkeypatch.delenv("DOTFILES_NO_TRUST")
    assert t_mod._trust_step([str(repo)]) == "run"
    said = []
    t_mod._trust_auto([str(repo)], say=said.append)
    assert len(said) == 2 and all("trusted" in s for s in said)
    assert t_mod._trust_step([str(repo)]) == "skip"
    t_mod._trust_auto([str(repo)], say=None)                               # new-land: silent
    t_mod._trust_auto([])                                                  # nothing registered


# ─── the surfaces that grew a trust step ────────────────────────────────────────

def test_doctor_warns_on_untrusted_registered_repos(t_mod):
    facts = {"trust_sync": {"claude": {"state": "pending", "add": ["/a", "/b"]},
                            "codex": {"state": "synced", "add": []},
                            "cursor": {"state": "absent", "add": []}}}
    out = t_mod._doctor_findings(facts)
    assert any("not trusted yet: claude (2)" in ln and "t trust --all" in ln for ln in out)
    assert not any("not trusted" in ln for ln in t_mod._doctor_findings({"trust_sync": None}))


def test_setup_trailer_states_everything_y_does(t_mod):
    hosts = [("mini", "me@mini")]
    assert t_mod._setup_trailer(hosts) == ("then trust the picked repos in each installed agent, "
                                           "then clone + register the picked repos on: mini")
    assert t_mod._setup_trailer([]) == "then trust the picked repos in each installed agent"
    assert t_mod._setup_trailer(hosts, trust=False) == t_mod._setup_hosts_trailer(hosts)
    assert t_mod._setup_trailer([], trust=False) is None


def test_new_plan_trust_step_follows_register(t_mod, tmp_path):
    path = str(tmp_path / "code" / "fresh")
    state = {"exists": False, "nonempty": False, "is_repo": False, "has_commits": False,
             "branch": None, "origin_url": None, "has_origin_main": False}
    common = ("fresh", "fresh", "me", True, path, state, ("absent", None), ("run", None), [])
    steps = [s["step"] for s in t_mod._new_plan(*common, trust="run")]
    assert steps[steps.index("register") + 1] == "trust"
    by = {s["step"]: s for s in t_mod._new_plan(*common, trust="skip")}
    assert by["trust"]["do"] == "skip" and "already trusted" in by["trust"]["label"]
    assert "trust" not in [s["step"] for s in t_mod._new_plan(*common)]    # opted out / no probe
    assert t_mod._new_trust({}, path) is None
    assert t_mod._new_trust({"trust": lambda p: "run"}, path) == "run"


def test_parity_matrix_has_the_trust_and_mode_rows(t_mod):
    rows = [r[0] for r in t_mod._AGENT_PARITY]
    assert any(r.startswith("t trust") for r in rows)
    assert any(r.startswith("default permission mode") for r in rows)
    assert "trust" in t_mod.IMPLEMENTED
    args = t_mod.build_parser().parse_args(["trust", "--all", "-q"])
    assert args.all and args.quiet and args.dirs == []


# ─── install.sh links-only, end to end ──────────────────────────────────────────

def _bridge(home, repos):
    d = home / ".config" / "t"
    d.mkdir(parents=True)
    (d / "config.sh").write_text("".join("DEV_REPOS[%s]=%s\n" % (k, v) for k, v in repos.items()))


def _relink(co, home, **extra):
    # bin/t finds the config bridge through $XDG_CONFIG_HOME when it is set — and a
    # GitHub runner sets it, so an unpinned sandbox run read the RUNNER's config dir,
    # found no repos, and trusted nothing (green locally, red in CI)
    return relink(co, home, XDG_CONFIG_HOME=str(home / ".config"), **extra)


def test_links_only_trusts_every_registered_repo(box, tmp_path):
    co, home = box
    api = _repo(tmp_path / "code" / "api")
    _bridge(home, {"api": str(api), "dot": str(co)})
    r = _relink(co, home)
    assert r.returncode == 0, r.stderr
    assert "trust:" not in r.stdout                       # no agent has run here yet: nothing to write
    assert not (home / ".claude.json").exists()
    (home / ".claude.json").write_text(json.dumps({"projects": {}}))
    (home / ".codex" / "config.toml").write_text('model = "gpt-6"\n')
    r = _relink(co, home)
    assert r.returncode == 0, r.stderr
    assert "trust: ✱ claude  trusted" in r.stdout and "trust: ⬡ codex   trusted" in r.stdout
    projects = json.loads((home / ".claude.json").read_text())["projects"]
    trusted = {os.path.realpath(k) for k, v in projects.items() if v.get("hasTrustDialogAccepted")}
    assert trusted == {os.path.realpath(str(api)), os.path.realpath(str(co))}
    assert 'trust_level = "trusted"' in (home / ".codex" / "config.toml").read_text()
    assert "trust:" not in _relink(co, home).stdout        # silent once in sync


def test_links_only_trust_opt_out_and_stub_bin_t(box, tmp_path):
    co, home = box
    _bridge(home, {"dot": str(co)})
    (home / ".claude.json").write_text(json.dumps({"projects": {}}))
    r = _relink(co, home, DOTFILES_NO_TRUST="1")
    assert r.returncode == 0 and json.loads((home / ".claude.json").read_text()) == {"projects": {}}
    (co / "bin" / "t").write_text("#!/usr/bin/env python3\nraise SystemExit(3)\n")   # an older checkout
    r = _relink(co, home)
    assert r.returncode == 0, r.stderr
    assert json.loads((home / ".claude.json").read_text()) == {"projects": {}}


def test_install_sh_runs_trust_in_the_links_only_path():
    text = (REPO_ROOT / "install.sh").read_text()
    call = text.index("\ninstall_agent_trust\n")
    assert text.index("install_agent_trust() {") < call < text.index('if [[ -n "${DOTFILES_LINKS_ONLY:-}" ]]; then\n    exit 0')
    assert "grep -q 'def cmd_trust'" in text
