"""Unit tests for the pure logic in bin/t.

Scope is deliberately the deterministic helpers — config parsing, path→repo
resolution, the capture-pane cleaner, row parsing/filtering. Subprocess/ssh/tmux
verbs (zsh_capture, _delegate, the cmd_* handlers) are out of scope by design.
"""

import json
import os
import time

import pytest


# ─── _unquote ──────────────────────────────────────────────────────────────────

def test_unquote_plain(t_mod):
    assert t_mod._unquote("/home/me/code") == "/home/me/code"


def test_unquote_quoted(t_mod):
    assert t_mod._unquote("'has space'") == "has space"
    assert t_mod._unquote('"double"') == "double"


def test_unquote_empty(t_mod):
    assert t_mod._unquote("") == ""
    assert t_mod._unquote("   ") == ""


def test_unquote_takes_first_token(t_mod):
    # shlex.split yields multiple words; _unquote keeps the first.
    assert t_mod._unquote("first second") == "first"


def test_unquote_malformed_falls_back(t_mod):
    # An unbalanced quote raises ValueError in shlex.split → return raw input.
    assert t_mod._unquote("'unbalanced") == "'unbalanced"


# ─── Config._load + _CFG_LINE ────────────────────────────────────────────────────

def _write_config(t_mod, tmp_path, monkeypatch, body):
    cfg_file = tmp_path / "config.sh"
    cfg_file.write_text(body)
    monkeypatch.setattr(t_mod, "CONFIG", str(cfg_file))
    return t_mod.Config()


def test_config_parses_arrays_and_scalars(t_mod, tmp_path, monkeypatch):
    cfg = _write_config(t_mod, tmp_path, monkeypatch, "\n".join([
        "DEV_REPOS[dotfiles]=/home/me/code/dotfiles",
        "DEV_REPOS[api]=/home/me/code/my-api",
        "DEV_BRANCHES[api]=dev/api-main",
        "REMOTE_HOSTS[mini]=mini.local",
        "DEV_WORKTREE[api]=0",
        "DEV_BRANCH=dev/custom",
        "DEV_WORKTREE_ROOT=/home/me/wt",
        "DEV_WORKTREE_DEFAULT=1",
    ]))
    assert cfg.repos == {"dotfiles": "/home/me/code/dotfiles", "api": "/home/me/code/my-api"}
    assert cfg.branches == {"api": "dev/api-main"}
    assert cfg.hosts == {"mini": "mini.local"}
    assert cfg.worktree == {"api": "0"}
    assert cfg.branch == "dev/custom"
    assert cfg.worktree_root == "/home/me/wt"
    assert cfg.worktree_default == "1"


def test_config_ignores_junk_lines(t_mod, tmp_path, monkeypatch):
    cfg = _write_config(t_mod, tmp_path, monkeypatch, "\n".join([
        "# a comment",
        "export SOMETHING=else",
        "DEV_REPOS[ok]=/x",
        "garbage line with no equals",
    ]))
    assert cfg.repos == {"ok": "/x"}


def test_config_missing_file_is_empty(t_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(t_mod, "CONFIG", str(tmp_path / "nope.sh"))
    cfg = t_mod.Config()
    assert cfg.repos == {}
    # Defaults survive an absent config.
    assert cfg.branch == "dev/claude-1"


# ─── Config.repo_of_dir / repo_dir_for_cwd ───────────────────────────────────────

def _config_with(t_mod, tmp_path, monkeypatch, repos, worktree_root=None):
    lines = ["DEV_REPOS[%s]=%s" % (k, v) for k, v in repos.items()]
    if worktree_root:
        lines.append("DEV_WORKTREE_ROOT=%s" % worktree_root)
    return _write_config(t_mod, tmp_path, monkeypatch, "\n".join(lines))


def test_repo_of_dir_canonical_and_subdir(t_mod, tmp_path, monkeypatch):
    cfg = _config_with(t_mod, tmp_path, monkeypatch,
                       {"dotfiles": "/code/dotfiles"}, worktree_root="/wt")
    assert cfg.repo_of_dir("/code/dotfiles") == ("dotfiles", "")
    assert cfg.repo_of_dir("/code/dotfiles/bin/sub") == ("dotfiles", "")


def test_repo_of_dir_worktree_path_yields_slot(t_mod, tmp_path, monkeypatch):
    cfg = _config_with(t_mod, tmp_path, monkeypatch,
                       {"dotfiles": "/code/dotfiles"}, worktree_root="/wt")
    # /wt/<basename>/<slot> → slot is captured, alias resolved by basename.
    assert cfg.repo_of_dir("/wt/dotfiles/3") == ("dotfiles", "3")
    assert cfg.repo_of_dir("/wt/dotfiles/3/bin") == ("dotfiles", "3")


def test_repo_of_dir_basename_wins_over_shortest(t_mod, tmp_path, monkeypatch):
    # Two aliases, same basename — the key equal to the basename wins.
    cfg = _config_with(t_mod, tmp_path, monkeypatch,
                       {"dot": "/code/dotfiles", "dotfiles": "/code/dotfiles"})
    assert cfg.repo_of_dir("/code/dotfiles") == ("dotfiles", "")


def test_repo_of_dir_shortest_key_when_no_basename_match(t_mod, tmp_path, monkeypatch):
    cfg = _config_with(t_mod, tmp_path, monkeypatch,
                       {"df": "/code/dotfiles", "dotrepo": "/code/dotfiles"})
    assert cfg.repo_of_dir("/code/dotfiles") == ("df", "")


def test_repo_of_dir_longest_path_prefix_wins(t_mod, tmp_path, monkeypatch):
    cfg = _config_with(t_mod, tmp_path, monkeypatch,
                       {"outer": "/code", "inner": "/code/inner"})
    assert cfg.repo_of_dir("/code/inner/x") == ("inner", "")


def test_repo_of_dir_no_match(t_mod, tmp_path, monkeypatch):
    cfg = _config_with(t_mod, tmp_path, monkeypatch, {"dotfiles": "/code/dotfiles"})
    assert cfg.repo_of_dir("/somewhere/else") == (None, None)


def test_repo_of_dir_worktree_basename_with_no_alias(t_mod, tmp_path, monkeypatch):
    # Path is under the worktree root but its basename matches no configured repo.
    cfg = _config_with(t_mod, tmp_path, monkeypatch,
                       {"dotfiles": "/code/dotfiles"}, worktree_root="/wt")
    assert cfg.repo_of_dir("/wt/unknown/2") == (None, None)


def test_repo_dir_for_cwd(t_mod, tmp_path, monkeypatch):
    cfg = _config_with(t_mod, tmp_path, monkeypatch, {"dotfiles": "/code/dotfiles"})
    assert cfg.repo_dir_for_cwd("/code/dotfiles/bin") == "/code/dotfiles"
    assert cfg.repo_dir_for_cwd("/elsewhere") == ""


# ─── _keep_sgr / _clean_capture ──────────────────────────────────────────────────

def test_keep_sgr_preserves_colour_drops_movement(t_mod):
    csi = t_mod._CSI

    def clean(b):
        return csi.sub(t_mod._keep_sgr, b)

    assert clean(b"\x1b[31m") == b"\x1b[31m"        # SGR colour kept
    assert clean(b"\x1b[0m") == b"\x1b[0m"          # reset kept
    assert clean(b"\x1b[2J") == b""                 # clear-screen dropped
    assert clean(b"\x1b[?25h") == b""               # cursor-show (private) dropped
    assert clean(b"\x1b[>4;2m") == b""              # private-marker m dropped


def test_clean_capture_keeps_colour(t_mod, tmp_path):
    log = tmp_path / "cap.log"
    log.write_bytes(b"\x1b[31mred\x1b[0m\n")
    lines = t_mod._clean_capture(str(log))
    assert any("\x1b[31m" in ln and "red" in ln for ln in lines)


def test_clean_capture_strips_non_colour_escapes(t_mod, tmp_path):
    log = tmp_path / "cap.log"
    # OSC title set, charset shifts, SO/SI, cursor toggles around plain text.
    log.write_bytes(
        b"\x1b]0;some title\x07"      # OSC title
        b"\x1b(Bplain\x0e\x0f"        # charset select + SO/SI shifts
        b"\x1b[?25hmore\x1b[?25l\n")
    lines = t_mod._clean_capture(str(log))
    joined = "".join(lines)
    assert "plain" in joined and "more" in joined
    assert "some title" not in joined
    assert "\x07" not in joined and "\x0e" not in joined and "\x0f" not in joined


def test_clean_capture_decodes_multibyte_around_control_bytes(t_mod, tmp_path):
    log = tmp_path / "cap.log"
    # A box-drawing char with an interleaved SO byte must still decode cleanly,
    # not become U+FFFD — control bytes are stripped from the raw bytes first.
    log.write_bytes("─".encode("utf-8") + b"\x0e" + "⏵".encode("utf-8") + b"\n")
    lines = t_mod._clean_capture(str(log))
    joined = "".join(lines)
    assert "─" in joined and "⏵" in joined
    assert "�" not in joined


def test_clean_capture_splits_repaint_rows(t_mod, tmp_path):
    log = tmp_path / "cap.log"
    log.write_bytes(b"row1\rrow2\nrow3\r\nrow4")
    lines = t_mod._clean_capture(str(log))
    assert [ln for ln in lines if ln] == ["row1", "row2", "row3", "row4"]


# ─── _truncate ───────────────────────────────────────────────────────────────────

def test_truncate_under_limit(t_mod):
    assert t_mod._truncate("short", 10) == "short"


def test_truncate_at_limit(t_mod):
    assert t_mod._truncate("exactly10!", 10) == "exactly10!"


def test_truncate_over_limit(t_mod):
    out = t_mod._truncate("waytoolong", 5)
    assert out == "wayt…"
    assert len(out) == 5


# ─── _parse_rows ─────────────────────────────────────────────────────────────────

def test_parse_rows_local(t_mod):
    text = "sid1\t/code/x\t2\tactive\t✓\tworking on y"
    rows = t_mod._parse_rows(text)
    assert rows == [dict(host="local", sid="sid1", cwd="/code/x", slot="2",
                         state="active", context="✓", summary="working on y")]


def test_parse_rows_host_prefixed(t_mod):
    text = "mini\tsid1\t/code/x\t2\tactive\t✓\tsummary"
    rows = t_mod._parse_rows(text, host_prefixed=True)
    assert rows[0]["host"] == "mini" and rows[0]["sid"] == "sid1"


def test_parse_rows_skips_short_and_blank(t_mod):
    text = "too\tshort\n\nsid\t/c\t1\tst\tctx\tsum"
    rows = t_mod._parse_rows(text)
    assert len(rows) == 1 and rows[0]["sid"] == "sid"


def test_parse_rows_host_prefixed_skips_short(t_mod):
    # Fewer than 7 tab fields in host-prefixed mode → skipped.
    text = "mini\tsid\t/c\t1\tst\tctx"   # only 6 fields
    assert t_mod._parse_rows(text, host_prefixed=True) == []


# ─── _scope_filter ───────────────────────────────────────────────────────────────

def _row(cwd):
    return dict(host="local", sid="s", cwd=cwd, slot="1",
                state="", context="", summary="")


def test_scope_filter_no_scope_passthrough(t_mod):
    rows = [_row("/anywhere")]
    assert t_mod._scope_filter(rows, "") == rows


def test_scope_filter_matches_repo_dir_and_subdir(t_mod):
    rows = [_row("/code/dotfiles"), _row("/code/dotfiles/bin"), _row("/other")]
    kept = t_mod._scope_filter(rows, "/code/dotfiles")
    assert [r["cwd"] for r in kept] == ["/code/dotfiles", "/code/dotfiles/bin"]


def test_scope_filter_matches_worktree_root(t_mod):
    rows = [_row("/wt/dotfiles/3"), _row("/other")]
    kept = t_mod._scope_filter(rows, "/code/dotfiles", wt_scope="/wt/dotfiles")
    assert [r["cwd"] for r in kept] == ["/wt/dotfiles/3"]


def test_scope_filter_matches_across_differing_homes(t_mod):
    # A Linux host's $HOME is /home/<u> while the local scope is /Users/<u> —
    # rows must match on the home-relative path (`code/dotfiles`), the same
    # cross-host key _dev_homerel uses on the zsh side.
    rows = [_row("/home/chris/code/dotfiles"),
            _row("/home/chris/code/dotfiles/bin"),
            _row("/home/chris/code/other")]
    kept = t_mod._scope_filter(rows, "/Users/chris/code/dotfiles")
    assert [r["cwd"] for r in kept] == ["/home/chris/code/dotfiles",
                                        "/home/chris/code/dotfiles/bin"]


def test_scope_filter_worktree_across_differing_homes(t_mod):
    rows = [_row("/home/chris/code/.worktrees/dotfiles/3"), _row("/home/chris/x")]
    kept = t_mod._scope_filter(rows, "/Users/chris/code/dotfiles",
                               wt_scope="/Users/chris/code/.worktrees/dotfiles")
    assert [r["cwd"] for r in kept] == ["/home/chris/code/.worktrees/dotfiles/3"]


def test_homerel_non_home_paths_pass_through(t_mod):
    assert t_mod._homerel("/opt/shared/repo") == "/opt/shared/repo"
    assert t_mod._homerel("") == ""


# ─── _infer_repo ─────────────────────────────────────────────────────────────────

def test_infer_repo_no_repo_for_cwd(t_mod, tmp_path, monkeypatch):
    cfg = _config_with(t_mod, tmp_path, monkeypatch, {"dotfiles": "/code/dotfiles"})
    monkeypatch.chdir(tmp_path)
    assert t_mod._infer_repo(cfg) is None


def test_infer_repo_exact_slot_log_wins(t_mod, tmp_path, monkeypatch):
    repo = tmp_path / "code" / "dotfiles"
    repo.mkdir(parents=True)
    logdir = tmp_path / ".tmux-logs"
    logdir.mkdir()
    # Two aliases on the same dir; the alias whose slot log exists should win.
    cfg = _config_with(t_mod, tmp_path, monkeypatch,
                       {"dot": str(repo), "dotfiles": str(repo)})
    (logdir / "dev-dotfiles-4.log").write_text("x")
    monkeypatch.setattr(t_mod, "HOME", str(tmp_path))
    monkeypatch.chdir(repo)
    assert t_mod._infer_repo(cfg, slot="4") == "dotfiles"


def test_infer_repo_newest_mtime_wins(t_mod, tmp_path, monkeypatch):
    repo = tmp_path / "code" / "dotfiles"
    repo.mkdir(parents=True)
    logdir = tmp_path / ".tmux-logs"
    logdir.mkdir()
    cfg = _config_with(t_mod, tmp_path, monkeypatch,
                       {"dot": str(repo), "dotfiles": str(repo)})
    old = logdir / "dev-dot-1.log"
    new = logdir / "dev-dotfiles-2.log"
    old.write_text("x")
    new.write_text("x")
    os.utime(str(old), (1000, 1000))
    os.utime(str(new), (2000, 2000))
    monkeypatch.setattr(t_mod, "HOME", str(tmp_path))
    monkeypatch.chdir(repo)
    assert t_mod._infer_repo(cfg) == "dotfiles"


def test_infer_repo_falls_back_to_key_when_no_logs(t_mod, tmp_path, monkeypatch):
    repo = tmp_path / "code" / "dotfiles"
    repo.mkdir(parents=True)
    (tmp_path / ".tmux-logs").mkdir()
    cfg = _config_with(t_mod, tmp_path, monkeypatch,
                       {"dot": str(repo), "dotfiles": str(repo)})
    monkeypatch.setattr(t_mod, "HOME", str(tmp_path))
    monkeypatch.chdir(repo)
    # No logs → basename match ("dotfiles") preferred over the shorter "dot".
    assert t_mod._infer_repo(cfg) == "dotfiles"


def test_infer_repo_shortest_alias_when_no_basename_match(t_mod, tmp_path, monkeypatch):
    repo = tmp_path / "code" / "dotfiles"
    repo.mkdir(parents=True)
    (tmp_path / ".tmux-logs").mkdir()
    # Neither alias equals the basename "dotfiles", and no logs exist → shortest.
    cfg = _config_with(t_mod, tmp_path, monkeypatch,
                       {"df": str(repo), "dotrepo": str(repo)})
    monkeypatch.setattr(t_mod, "HOME", str(tmp_path))
    monkeypatch.chdir(repo)
    assert t_mod._infer_repo(cfg) == "df"


# ─── t setup: _local_entries ─────────────────────────────────────────────────────

def test_local_entries_parses_live_lines(t_mod):
    repos, hosts, tbeam = t_mod._local_entries("\n".join([
        'DEV_REPOS[api]="$HOME/code/my-api"',
        "REMOTE_HOSTS[mini]=chris@mini.local",
        "export TBEAM_HOST=chris@mini.local",
    ]))
    assert repos == {"api": '"$HOME/code/my-api"'}
    assert hosts == {"mini": "chris@mini.local"}
    assert tbeam is True


def test_local_entries_skips_commented_examples(t_mod):
    # The shapes .zshrc.local.example ships commented out must not register.
    repos, hosts, tbeam = t_mod._local_entries("\n".join([
        "# DEV_REPOS[api]=$HOME/code/my-api",
        "# REMOTE_HOSTS[mini]=my-mini",
        "# export TBEAM_HOST=my-remote",
        "  DEV_REPOS[web]=/code/web",   # leading whitespace is still live
    ]))
    assert repos == {"web": "/code/web"}
    assert hosts == {} and tbeam is False


def test_local_entries_empty_text(t_mod):
    assert t_mod._local_entries("") == ({}, {}, False)


# ─── t setup: _expand_home ───────────────────────────────────────────────────────

def test_expand_home_forms(t_mod, monkeypatch):
    monkeypatch.setattr(t_mod, "HOME", "/Users/me")
    assert t_mod._expand_home('"$HOME/code/x"') == "/Users/me/code/x"
    assert t_mod._expand_home("${HOME}/code/x") == "/Users/me/code/x"
    assert t_mod._expand_home("/abs/path") == "/abs/path"


def test_expand_home_tilde(t_mod):
    assert t_mod._expand_home("~/code/x") == os.path.expanduser("~/code/x")


# ─── t setup: _scan_repos ────────────────────────────────────────────────────────

def _mk_repo(root, name, git_file=False):
    d = root / name
    d.mkdir(parents=True)
    if git_file:
        (d / ".git").write_text("gitdir: elsewhere")
    else:
        (d / ".git").mkdir()
    return d


def test_scan_repos_finds_git_dir_and_git_file(t_mod, tmp_path):
    a = _mk_repo(tmp_path, "a")
    b = _mk_repo(tmp_path, "b", git_file=True)   # worktree/submodule .git file
    assert t_mod._scan_repos([str(tmp_path)], set()) == [str(a), str(b)]


def test_scan_repos_prunes_below_repo_root(t_mod, tmp_path):
    a = _mk_repo(tmp_path, "a")
    _mk_repo(a, "vendored")   # nested repo inside a — never offered
    assert t_mod._scan_repos([str(tmp_path)], set()) == [str(a)]


def test_scan_repos_skips_hidden_and_skip_paths(t_mod, tmp_path):
    a = _mk_repo(tmp_path, "a")
    _mk_repo(tmp_path / ".worktrees", "hiddenrepo")   # under a hidden dir
    reg = _mk_repo(tmp_path, "registered")
    assert t_mod._scan_repos([str(tmp_path)], {str(reg)}) == [str(a)]


def test_scan_repos_skips_registered_via_symlink(t_mod, tmp_path):
    a = _mk_repo(tmp_path, "a")
    link = tmp_path / "alink"
    link.symlink_to(a)
    # Registered under the symlinked path → the real path is still skipped.
    assert t_mod._scan_repos([str(tmp_path)], {str(link)}) == []


def test_scan_repos_depth_cap(t_mod, tmp_path):
    deep = tmp_path / "l1" / "l2" / "l3"
    _mk_repo(deep, "toodeep")
    shallow = _mk_repo(tmp_path / "l1", "ok")
    assert t_mod._scan_repos([str(tmp_path)], set()) == [str(shallow)]


def test_scan_repos_missing_dir_yields_nothing(t_mod, tmp_path):
    assert t_mod._scan_repos([str(tmp_path / "nope")], set()) == []


def test_scan_repos_top_is_repo(t_mod, tmp_path):
    a = _mk_repo(tmp_path, "a")
    assert t_mod._scan_repos([str(a)], set()) == [str(a)]


# ─── t setup: _propose_aliases / _propose_aliases_hosts ──────────────────────────

def test_propose_aliases_basename(t_mod):
    props, collided = t_mod._propose_aliases(["/code/api"], set())
    assert props == {"/code/api": "api"} and collided == set()


def test_propose_aliases_parent_qualifier_on_taken(t_mod):
    props, collided = t_mod._propose_aliases(["/work/api"], {"api"})
    assert props == {"/work/api": "work-api"}
    assert collided == {"/work/api"}


def test_propose_aliases_batch_duplicate(t_mod):
    props, collided = t_mod._propose_aliases(["/code/api", "/work/api"], set())
    assert props == {"/code/api": "api", "/work/api": "work-api"}
    assert collided == {"/work/api"}


def test_propose_aliases_suffixes_qualified_alias(t_mod):
    # Qualified name also taken → numeric suffix on the QUALIFIED alias, keeping
    # the parent context (work-api-2, not api-2).
    props, collided = t_mod._propose_aliases(["/work/api"], {"api", "work-api"})
    assert props == {"/work/api": "work-api-2"}
    assert collided == {"/work/api"}


def test_propose_aliases_hosts_dot_label(t_mod):
    props, skipped = t_mod._propose_aliases_hosts(["studio.local", "mini"], set())
    assert props == {"studio": "studio.local", "mini": "mini"}
    assert skipped == []


def test_propose_aliases_hosts_full_name_fallback_and_skip(t_mod):
    props, skipped = t_mod._propose_aliases_hosts(
        ["studio.local", "studio.remote"], {"studio"})
    assert props == {"studio.local": "studio.local", "studio.remote": "studio.remote"}
    props, skipped = t_mod._propose_aliases_hosts(["studio"], {"studio"})
    assert props == {} and skipped == ["studio"]


# ─── t setup: _homeify / _hostval / _setup_block ─────────────────────────────────

def test_homeify_under_home(t_mod, monkeypatch):
    monkeypatch.setattr(t_mod, "HOME", "/Users/me")
    assert t_mod._homeify("/Users/me/code/x") == '"$HOME/code/x"'


def test_homeify_outside_home(t_mod, monkeypatch):
    monkeypatch.setattr(t_mod, "HOME", "/Users/me")
    assert t_mod._homeify("/Volumes/work/x") == '"/Volumes/work/x"'
    # A sibling dir sharing the prefix string is NOT under home.
    assert t_mod._homeify("/Users/meep/x") == '"/Users/meep/x"'


def test_hostval_bare_vs_quoted(t_mod):
    assert t_mod._hostval("chris@mini.local") == "chris@mini.local"
    assert t_mod._hostval("host with space") == '"host with space"'


def test_setup_block_exact_format(t_mod, monkeypatch):
    monkeypatch.setattr(t_mod, "HOME", "/Users/me")
    block = t_mod._setup_block(
        {"dotfiles": "/Users/me/code/dotfiles", "scratch": "/Volumes/work/scratch"},
        {"mini": "chris@mini.local"}, "chris@mini.local", "2026-08-11")
    assert block == (
        "# ── added by `t setup` (2026-08-11) ──\n"
        'DEV_REPOS[dotfiles]="$HOME/code/dotfiles"\n'
        'DEV_REPOS[scratch]="/Volumes/work/scratch"\n'
        "REMOTE_HOSTS[mini]=chris@mini.local\n"
        "export TBEAM_HOST=chris@mini.local\n")


def test_setup_block_repos_only(t_mod, monkeypatch):
    monkeypatch.setattr(t_mod, "HOME", "/Users/me")
    block = t_mod._setup_block({"x": "/Users/me/x"}, {}, None, "2026-08-11")
    assert block == ("# ── added by `t setup` (2026-08-11) ──\n"
                     'DEV_REPOS[x]="$HOME/x"\n')


# ─── t setup: _parse_ssh_hosts ───────────────────────────────────────────────────

def test_parse_ssh_hosts_multi_name_and_wildcards(t_mod, tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("\n".join([
        "# a comment",
        "",
        "Host mini studio.local",
        "  HostName mini.example.com",
        "Host *",                       # stock wildcard — skipped
        "host lower ?maybe !negated",   # keyword case-insensitive; ?/! skipped
        'Include "unbalanced',          # shlex chokes → whitespace-split fallback
    ]))
    assert t_mod._parse_ssh_hosts(str(cfg)) == ["mini", "studio.local", "lower"]


def test_parse_ssh_hosts_missing_file(t_mod, tmp_path):
    assert t_mod._parse_ssh_hosts(str(tmp_path / "nope")) == []


def test_parse_ssh_hosts_follows_include_glob(t_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(t_mod, "HOME", str(tmp_path))
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    (ssh / "config").write_text("Include extra.d/*.conf\nHost main\n")
    (ssh / "extra.d").mkdir()
    (ssh / "extra.d" / "a.conf").write_text("Host inca\n")
    (ssh / "extra.d" / "b.conf").write_text("Host incb\n")
    assert t_mod._parse_ssh_hosts(str(ssh / "config")) == ["inca", "incb", "main"]


def test_parse_ssh_hosts_include_cycle_safe(t_mod, tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.write_text(f"Include {b}\nHost hosta\n")
    b.write_text(f"Include {a}\nHost hostb\n")
    assert t_mod._parse_ssh_hosts(str(a)) == ["hostb", "hosta"]


def test_parse_ssh_hosts_dedupes(t_mod, tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("Host mini\nHost mini other\n")
    assert t_mod._parse_ssh_hosts(str(cfg)) == ["mini", "other"]


# ─── t setup: item model (_setup_items / _selectable / _setup_result) ────────────

def _items(t_mod, **kw):
    args = dict(repo_cands=[("/code/api", "api")], reg_repos={"dot": "/code/dot"},
                host_cands=[("studio", "studio.local")], reg_hosts={"mini": "mini.local"},
                tbeam_default="studio.local", stale=["old"], roots_label="~/code")
    args.update(kw)
    return t_mod._setup_items(**args)


def test_setup_items_full_structure(t_mod):
    items = _items(t_mod)
    kinds = [(it["t"], it.get("kind")) for it in items]
    assert kinds == [
        ("header", None), ("toggle", "repo"), ("locked", None),          # repos
        ("spacer", None), ("header", None), ("toggle", "host"),
        ("toggle", "addhost"), ("locked", None),                         # hosts
        ("spacer", None), ("header", None), ("toggle", "stale"), ("toggle", "tbeam"),
    ]
    assert items[0]["label"] == "REPOS · ~/code"
    assert all(not it["checked"] for it in items if it["t"] == "toggle")


def test_setup_items_empty_sections_get_notes(t_mod):
    items = _items(t_mod, repo_cands=[], host_cands=[], tbeam_default=None, stale=[])
    notes = [it["label"] for it in items if it["t"] == "note"]
    assert notes == ["no new checkouts found"]
    # the hosts section always keeps its selectable add-a-host action row
    assert [it["kind"] for it in items if it["t"] == "toggle"] == ["addhost"]
    # no OPTIONS section when there is nothing to put in it
    assert all(it.get("label") != "OPTIONS" for it in items if it["t"] == "header")


def test_selectable_indices(t_mod):
    items = _items(t_mod)
    idx = t_mod._selectable(items)
    assert [items[i]["kind"] for i in idx] == ["repo", "host", "addhost", "stale", "tbeam"]


def test_setup_result_maps_checked_toggles(t_mod):
    items = _items(t_mod)
    for it in items:
        if it["t"] == "toggle":
            it["checked"] = True
    repos, hosts, tbeam, stale = t_mod._setup_result(items)
    assert repos == {"api": "/code/api"}
    assert hosts == {"studio": "studio.local"}
    assert tbeam == "studio.local" and stale is True


def test_setup_result_unchecked_is_empty(t_mod):
    assert t_mod._setup_result(_items(t_mod)) == ({}, {}, None, False)


def test_host_insert_at_before_addhost_row(t_mod):
    items = _items(t_mod)
    at = t_mod._host_insert_at(items)
    assert items[at - 1]["kind"] == "host"      # right after the host toggle
    assert items[at]["kind"] == "addhost"       # directly above the action row


def test_host_insert_at_without_addhost_row(t_mod):
    items = [it for it in _items(t_mod) if it.get("kind") != "addhost"]
    at = t_mod._host_insert_at(items)
    assert items[at - 1]["kind"] == "host"
    assert items[at]["t"] == "locked"           # before the registered rows


def test_parse_host_entry_forms(t_mod):
    assert t_mod._parse_host_entry("mini=chris@mini.local", set()) == ("mini", "chris@mini.local")
    assert t_mod._parse_host_entry("chris@studio.local", set()) == ("studio", "chris@studio.local")
    alias, err = t_mod._parse_host_entry("mini=x", {"mini"})
    assert alias is None and "taken" in err


def test_tilde(t_mod, monkeypatch):
    monkeypatch.setattr(t_mod, "HOME", "/Users/me")
    assert t_mod._tilde("/Users/me/code/x") == "~/code/x"
    assert t_mod._tilde("/opt/x") == "/opt/x"


# ─── t setup: _comment_stale / _append_local ─────────────────────────────────────

def test_comment_stale_targets_only_named_keys(t_mod):
    text = ("# header\n"
            'DEV_REPOS[api]="$HOME/code/my-api"\n'
            'DEV_REPOS[web]="$HOME/code/my-web"\n'
            "REMOTE_HOSTS[api]=whatever\n")
    out = t_mod._comment_stale(text, {"api"})
    assert out == ("# header\n"
                   '# (stale — t setup) DEV_REPOS[api]="$HOME/code/my-api"\n'
                   'DEV_REPOS[web]="$HOME/code/my-web"\n'
                   "REMOTE_HOSTS[api]=whatever\n")


def test_comment_stale_preserves_missing_trailing_newline(t_mod):
    out = t_mod._comment_stale("DEV_REPOS[a]=/x", {"nomatch"})
    assert out == "DEV_REPOS[a]=/x"


def test_append_local_separator_and_newline_normalization(t_mod, tmp_path):
    f = tmp_path / "local"
    f.write_text("existing content")     # no trailing newline
    t_mod._append_local(str(f), "BLOCK\n")
    assert f.read_text() == "existing content\n\nBLOCK\n"


def test_append_local_creates_missing_file_with_header(t_mod, tmp_path):
    f = tmp_path / "local"
    t_mod._append_local(str(f), "BLOCK\n")
    text = f.read_text()
    assert text.startswith("# ~/.zshrc.local")
    assert text.endswith("\n\nBLOCK\n")


# ─── doctor: _parse_install_links / _doctor_findings ───────────────────────────

def test_parse_install_links_extracts_pairs(t_mod):
    text = (
        'link "$LINK_SRC/.zshrc"               "$HOME/.zshrc"\n'
        'link "$LINK_SRC/bin/t"                "$HOME/bin/t"\n'
        'link "$LINK_SRC/ssh/config" "$HOME/.ssh/dotfiles.conf"\n'
    )
    assert t_mod._parse_install_links(text) == [
        (".zshrc", "$HOME/.zshrc"),
        ("bin/t", "$HOME/bin/t"),
        ("ssh/config", "$HOME/.ssh/dotfiles.conf"),
    ]


def test_parse_install_links_accepts_indented_calls(t_mod):
    # the calls live inside install.sh's link_all(), so they are indented; anchoring
    # `link` at column 0 made doctor report 0 managed links
    text = (
        'link_all() {\n'
        '    link "$LINK_SRC/.zshrc"     "$HOME/.zshrc"\n'
        '\tlink "$LINK_SRC/bin/t"       "$HOME/bin/t"\n'
        '}\n'
    )
    assert t_mod._parse_install_links(text) == [
        (".zshrc", "$HOME/.zshrc"),
        ("bin/t", "$HOME/bin/t"),
    ]


def test_parse_install_links_indented_mentions_still_skipped(t_mod):
    # indentation is allowed, but the line must still START with `link`
    text = (
        '    # link "$LINK_SRC/dead" "$HOME/dead"\n'
        '    echo link "$LINK_SRC/nope" "$HOME/nope"\n'
        '    link "$LINK_SRC/real" "$HOME/real"\n'
    )
    assert t_mod._parse_install_links(text) == [("real", "$HOME/real")]


def test_parse_install_links_matches_the_real_install_sh(t_mod):
    # guards the coupling directly: whatever install.sh looks like, doctor must find
    # every managed link in it
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(t_mod.__file__)))
    with open(os.path.join(root, "install.sh")) as f:
        pairs = t_mod._parse_install_links(f.read())
    assert len(pairs) >= 12
    dsts = [d for _, d in pairs]
    assert "$HOME/.tmux.conf" in dsts
    assert "$HOME/.ssh/dotfiles.conf" in dsts   # the one outside the main block


def test_parse_install_links_skips_comments_and_other_lines(t_mod):
    text = (
        '# link "$LINK_SRC/dead" "$HOME/dead"\n'
        'echo link "$LINK_SRC/nope" "$HOME/nope"\n'
        'link "$LINK_SRC/.tmux.conf"           "$HOME/.tmux.conf"\n'
    )
    assert t_mod._parse_install_links(text) == [(".tmux.conf", "$HOME/.tmux.conf")]


def test_doctor_findings_healthy(t_mod):
    facts = {"links": [("/h/.zshrc", "ok")], "behind": 0, "conf_exists": True,
             "conf_is_link": True, "conf_mouse_on": True, "tmux_running": True,
             "mouse": "on", "history_limit": 50000, "panes": []}
    assert t_mod._doctor_findings(facts) == ["✓ nothing suspicious found"]


def test_doctor_findings_bad_links_and_behind(t_mod):
    facts = {"links": [("/h/.zshrc", "ok"), ("/h/.tmux.conf", "missing")], "behind": 2}
    out = t_mod._doctor_findings(facts)
    assert any("1 managed link(s) not in place" in l and ".tmux.conf (missing)" in l for l in out)
    # the fix is a runnable command, not a path to hand-type
    assert any("run dots" in l for l in out)
    assert any("behind origin/main — run dots" in l and "2 commit(s)" in l for l in out)


def test_doctor_findings_conf_real_file(t_mod):
    out = t_mod._doctor_findings({"conf_exists": True, "conf_is_link": False})
    assert any("real file, not the repo-managed symlink" in l for l in out)


def test_doctor_findings_mouse_off_config_loaded_vs_not(t_mod):
    # conf says on but server off → the server predates the config: source-file hint
    out = t_mod._doctor_findings({"tmux_running": True, "mouse": "off", "conf_mouse_on": True})
    assert any("tmux source-file" in l for l in out)
    # conf does not say on → the config itself is missing the setting: install.sh hint
    out = t_mod._doctor_findings({"tmux_running": True, "mouse": "off", "conf_mouse_on": False})
    assert any("stale scrollback" in l for l in out)


def test_doctor_findings_mouse_capture_panes_and_stale_history(t_mod):
    panes = [{"session": "dev-ff-1", "cmd": "claude", "mouse": True, "alt": True, "hist": 2000},
             {"session": "dev-ff-2", "cmd": "zsh", "mouse": False, "alt": False, "hist": 2000}]
    facts = {"tmux_running": True, "mouse": "on", "history_limit": 50000, "panes": panes}
    out = t_mod._doctor_findings(facts)
    assert any("1 pane(s) capture the wheel" in l for l in out)
    assert any("old history-limit (2000)" in l for l in out)


def test_doctor_findings_no_capture_note_when_mouse_off(t_mod):
    # with mouse off the wheel never reaches the pane app, so the capture note
    # would be noise — only the mouse-off warning should show
    panes = [{"session": "s", "cmd": "claude", "mouse": True, "alt": True, "hist": 2000}]
    out = t_mod._doctor_findings({"tmux_running": True, "mouse": "off",
                                  "conf_mouse_on": True, "panes": panes})
    assert not any("capture the wheel" in l for l in out)


# ─── doctor: _effective_tui / the non-tmux wheel-as-arrows rule ────────────────

def test_effective_tui_unset_is_not_fullscreen(t_mod):
    # `tui` is optional with no schema default: unset means the classic
    # renderer, so it must never resolve to fullscreen
    assert t_mod._effective_tui(None, {}) is None
    assert t_mod._effective_tui("default", {}) == "default"
    assert t_mod._effective_tui("fullscreen", {}) == "fullscreen"


def test_effective_tui_env_overrides(t_mod):
    # NO_FLICKER is documented as equivalent to fullscreen
    assert t_mod._effective_tui(None, {"CLAUDE_CODE_NO_FLICKER": "1"}) == "fullscreen"
    # DISABLE_ALTERNATE_SCREEN forces the main screen and outranks everything
    assert t_mod._effective_tui(
        "fullscreen", {"CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN": "1"}) == "default"
    assert t_mod._effective_tui(
        None, {"CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN": "1",
               "CLAUDE_CODE_NO_FLICKER": "1"}) == "default"


def test_env_on_treats_falsey_strings_as_off(t_mod):
    for v in ("", "0", "false", "FALSE", "  "):
        assert t_mod._env_on({"X": v}, "X") is False
    for v in ("1", "true", "yes"):
        assert t_mod._env_on({"X": v}, "X") is True
    assert t_mod._env_on({}, "X") is False


def test_doctor_findings_wheel_as_arrows_outside_tmux(t_mod):
    out = t_mod._doctor_findings(
        {"in_tmux": False, "term_program": "Apple_Terminal", "tui": "fullscreen"})
    assert any("arrow keys on the alternate screen" in l for l in out)
    assert any("/tui default" in l for l in out)


def test_doctor_findings_wheel_rule_is_narrow(t_mod):
    def fires(**over):
        facts = {"in_tmux": False, "term_program": "Apple_Terminal", "tui": "fullscreen"}
        facts.update(over)
        return any("arrow keys on the alternate screen" in l
                   for l in t_mod._doctor_findings(facts))

    assert fires()
    # inside tmux the pane-capture note already covers it
    assert not fires(in_tmux=True)
    # classic renderer scrolls native scrollback fine
    assert not fires(tui="default")
    # unset renderer must stay quiet (this is the regression that would spam
    # every user who never opted into fullscreen)
    assert not fires(tui=None)
    # terminals that report the wheel properly are not affected
    assert not fires(term_program="iTerm.app")
    assert not fires(term_program=None)


# ─── the dev URL a slot is testable at (_dev_url, the sessions MCP rows) ────────

def _dev_url(t_mod, monkeypatch, key, cwd, live=()):
    monkeypatch.setattr(t_mod, "_port_is_live", lambda p, *a, **k: p in live)
    return t_mod._dev_url(key, cwd)


def test_dev_url_uses_the_slot_port(t_mod, monkeypatch, tmp_path):
    # The slot number IS the port offset — dev-worktree.sh binds 5200+N — so slot 12
    # is testable at :5212 and nowhere else.
    (tmp_path / "package.json").write_text("{}")
    assert _dev_url(t_mod, monkeypatch, "ff-12", str(tmp_path), live=(5212,)) \
        == "http://localhost:5212"


def test_dev_url_is_none_when_nothing_is_listening(t_mod, monkeypatch, tmp_path):
    # A URL that refuses the connection is worse than no URL.
    assert _dev_url(t_mod, monkeypatch, "ff-12", str(tmp_path)) is None


def test_dev_url_falls_back_to_the_canonical_port_for_a_primary_checkout(
        t_mod, monkeypatch, tmp_path):
    # No slot in the key → the plain dev port, which is where a primary checkout
    # serves from.
    (tmp_path / "package.json").write_text("{}")
    assert _dev_url(t_mod, monkeypatch, "ff", str(tmp_path), live=(5173,)) \
        == "http://localhost:5173"


def test_dev_url_never_hands_a_slot_the_shared_port(t_mod, monkeypatch, tmp_path):
    # ff-12's server was down and the bar confidently said :5173 — some OTHER
    # checkout's app. A slot has one port; down means no URL.
    (tmp_path / "package.json").write_text("{}")
    assert _dev_url(t_mod, monkeypatch, "ff-12", str(tmp_path), live=(5173,)) is None


def test_dev_url_prefers_the_slot_port_over_the_shared_one(t_mod, monkeypatch, tmp_path):
    (tmp_path / "package.json").write_text("{}")
    assert _dev_url(t_mod, monkeypatch, "ff-12", str(tmp_path), live=(5173, 5212)) \
        == "http://localhost:5212"


def test_dev_url_will_not_claim_the_shared_port_without_a_package_json(
        t_mod, monkeypatch, tmp_path):
    # :5173 is whoever got there first. A checkout with no dev server of its own must
    # not point the user at someone else's app.
    assert _dev_url(t_mod, monkeypatch, "dotfiles", str(tmp_path), live=(5173,)) is None


def test_dev_url_slot_port_needs_a_dev_server_too(t_mod, monkeypatch, tmp_path):
    # Ports are 5200+N across EVERY repo: dotfiles slot 3 must not claim ff slot 3's
    # vite on :5203 — a CLI repo has nothing to serve.
    assert _dev_url(t_mod, monkeypatch, "dotfiles-3", str(tmp_path), live=(5203,)) is None


def test_dev_url_ignores_a_non_numeric_trailing_segment(t_mod, monkeypatch, tmp_path):
    assert _dev_url(t_mod, monkeypatch, "scratch", str(tmp_path), live=(5200,)) is None


# ─── the shared slot row (t ls + every slot picker) ─────────────────────────────

def test_slot_line_marks_and_columns(t_mod):
    st = t_mod.Style(tty=False)
    row = {"host": "local", "slot": "ff-3", "state": "attached", "context": "active", "summary": "x"}
    assert t_mod._slot_line(row, st, 7, 20) == "● ✓     ff-3    x"
    row = dict(row, state="detached", context="idle")
    assert t_mod._slot_line(row, st, 7, 20) == "○       ff-3    x"
    # a dead slot has no marks — its summary carries the story — and is truncated to avail
    row = dict(row, state="dead", context="none", summary="a" * 30)
    line = t_mod._slot_line(row, st, 7, 20)
    assert line.startswith("        ff-3    ") and line.endswith("…")
    assert len(line) == 8 + 7 + 1 + 20
    # the t ls -r HOST column; a local row leaves it blank
    assert t_mod._slot_line(dict(row, host="mini"), st, 7, 20, host_w=4).startswith("        mini ff-3")
    assert t_mod._slot_line(dict(row, host="local"), st, 7, 20, host_w=4).startswith(" " * 13 + "ff-3")


def test_slot_line_colours_only_through_the_style(t_mod):
    st = t_mod.Style(tty=True)
    row = {"host": "local", "slot": "ff-3", "state": "attached", "context": "active", "summary": "x"}
    line = t_mod._slot_line(row, st, 7, 20)
    assert "\033[32m●\033[0m" in line and "\033[36m✓\033[0m" in line


def test_parse_rows_carries_a_dead_slot_row(t_mod):
    rows = t_mod._parse_rows("abc\t/wt/ff/2\tff-2\tdead\tnone\told chart · Aug 21 14:02\n")
    assert rows[0]["state"] == "dead" and rows[0]["summary"].endswith("14:02")


# ─── t new: pure helpers (name/url/slug, gh state, alias check, plan, hosts) ────

import pytest
from types import SimpleNamespace as _NS


def _new_state(**kw):
    s = {"exists": False, "nonempty": False, "is_repo": False, "has_commits": False,
         "branch": None, "origin_url": None, "has_origin_main": False}
    s.update(kw)
    return s


_GH_OK = {"allow_auto_merge": True, "delete_branch_on_merge": True, "allow_squash_merge": True,
          "allow_merge_commit": False, "allow_rebase_merge": False, "private": True}
_GH_HIVE = {"allow_auto_merge": True, "delete_branch_on_merge": False, "allow_squash_merge": True,
            "allow_merge_commit": True, "allow_rebase_merge": True, "private": True}


@pytest.mark.parametrize("name", ["ok", "my-tool_1.2", "x.y", "A" * 100, "Mixed", ".github", ".x"])
def test_new_validate_name_accepts(t_mod, name):
    assert t_mod._new_validate_name(name) is None


@pytest.mark.parametrize("name", ["", None, "a/b", "-x", ".", "..", "...", "x.git", "a b", "a" * 101, "ünï"])
def test_new_validate_name_rejects(t_mod, name):
    assert t_mod._new_validate_name(name)


def test_new_name_warnings_only_for_uppercase(t_mod):
    assert t_mod._new_name_warnings("lower") == []
    assert len(t_mod._new_name_warnings("Upper")) == 1


def test_new_path_and_clone_url(t_mod, monkeypatch):
    monkeypatch.setattr(t_mod, "HOME", "/Users/me")
    assert t_mod._new_path("x") == "/Users/me/code/x"
    assert t_mod._new_clone_url("agenthangar", "x") == "git@github.com:agenthangar/x.git"


@pytest.mark.parametrize("url,slug", [
    ("git@github.com:agenthangar/x.git", "agenthangar/x"),
    ("ssh://git@github.com/o/n.git", "o/n"),
    ("https://github.com/o/n", "o/n"),
    ("https://github.com/o/n.git/", "o/n"),
    ("git@gitlab.com:o/n.git", None),
    ("/srv/git/n.git", None),
    ("", None),
    (None, None),
])
def test_new_repo_slug_forms(t_mod, url, slug):
    assert t_mod._new_repo_slug(url) == slug


def test_new_gh_repo_state_classifies(t_mod):
    kind, info = t_mod._new_gh_repo_state(0, '{"private": true}', "")
    assert kind == "present" and info == {"private": True}
    assert t_mod._new_gh_repo_state(1, "", "gh: Not Found (HTTP 404)") == ("absent", None)
    kind, msg = t_mod._new_gh_repo_state(4, "", "To get started with GitHub CLI, please run:  gh auth login")
    assert kind == "error" and "gh auth login" in msg
    kind, msg = t_mod._new_gh_repo_state(1, "", "error: boom\nHTTP 403: forbidden")
    assert (kind, msg) == ("error", "HTTP 403: forbidden")
    assert t_mod._new_gh_repo_state(0, "not json", "")[0] == "error"
    assert t_mod._new_gh_repo_state(127, "", "")[0] == "error"


def test_new_settings_ok_shapes(t_mod):
    assert t_mod._new_settings_ok(_GH_OK)
    assert not t_mod._new_settings_ok(_GH_HIVE)
    assert not t_mod._new_settings_ok({})


def test_new_whence_kind(t_mod):
    assert t_mod._new_whence_kind("test: builtin") == "builtin"
    assert t_mod._new_whence_kind("t: function\n") == "function"
    assert t_mod._new_whence_kind("x: none") is None
    assert t_mod._new_whence_kind("") is None


def test_new_alias_check_outcomes(t_mod, monkeypatch):
    monkeypatch.setattr(t_mod, "HOME", "/Users/me")
    f = {"api": '"$HOME/code/api"'}
    cfg = {"cf": "/Users/me/code/cashfwd"}
    assert t_mod._new_alias_check("api", "/Users/me/code/api", f, cfg)[0] == "skip"
    kind, msg = t_mod._new_alias_check("api", "/Users/me/code/other", f, cfg)
    assert kind == "error" and "~/code/api" in msg
    assert t_mod._new_alias_check("cf", "/Users/me/code/other", f, cfg)[0] == "error"   # cache-only collision
    kind, msg = t_mod._new_alias_check("test", "/Users/me/code/test", f, cfg, "builtin")
    assert kind == "error" and "shadow" in msg
    assert t_mod._new_alias_check("fresh", "/Users/me/code/fresh", f, cfg, None) == ("run", None)
    # a second alias for an already-registered path is allowed (dot + dotfiles)
    assert t_mod._new_alias_check("api2", "/Users/me/code/api", f, cfg)[0] == "run"
    assert t_mod._new_alias_check("bad key", "/Users/me/code/x", f, cfg)[0] == "error"
    assert t_mod._new_alias_check("", "/Users/me/code/x", f, cfg)[0] == "error"


def test_new_owner_rows_default_first_and_you(t_mod):
    rows = t_mod._new_owner_rows("me", ["cashfwd", "agenthangar"], "agenthangar")
    assert rows == [("agenthangar", "agenthangar"), ("cashfwd", "cashfwd"), ("me", "me (you)")]
    assert t_mod._new_owner_rows("me", [], "agenthangar") == [("me", "me (you)")]   # not a member → not offered
    assert t_mod._new_owner_rows(None, [], "agenthangar") == []


def test_new_pick_hosts(t_mod):
    cfg = {"openclaw": "u@oc", "mini": "u@mini"}
    assert t_mod._new_pick_hosts(cfg, None, True) == ([], None)
    assert t_mod._new_pick_hosts(cfg, None, False) == ([("mini", "u@mini"), ("openclaw", "u@oc")], None)
    assert t_mod._new_pick_hosts(cfg, "mini", False) == ([("mini", "u@mini")], None)
    rows, err = t_mod._new_pick_hosts(cfg, "mini,nope", False)
    assert rows == [] and "nope" in err


def test_new_answers_from_flags(t_mod):
    a = t_mod._new_answers(_NS(name="x", owner=None, public=True, private=False, alias=None,
                               hosts="mini, openclaw", no_hosts=False))
    assert a == {"name": "x", "owner": None, "visibility": "public", "alias": None,
                 "hosts": ["mini", "openclaw"], "prompt": None}
    a = t_mod._new_answers(_NS(name=None, owner="o", public=False, private=True, alias="k",
                               hosts=None, no_hosts=True, prompt="  a cli for X  "))
    assert a == {"name": None, "owner": "o", "visibility": "private", "alias": "k", "hosts": [],
                 "prompt": "a cli for X"}
    assert t_mod._new_answers(_NS(name=None, owner=None, public=False, private=False, alias=None,
                                  hosts=None, no_hosts=False))["hosts"] is None


def test_new_name_input_one_token_is_a_name_anything_else_a_prompt(t_mod):
    assert t_mod._new_name_input("  cashfwd ") == ("name", "cashfwd")
    assert t_mod._new_name_input("-bad") == ("name", "-bad")     # still validated as a name
    assert t_mod._new_name_input("a cli that forecasts cash") == ("prompt", "a cli that forecasts cash")
    assert t_mod._new_name_input("two\twords") == ("prompt", "two\twords")


def test_new_name_prompt_carries_data_not_rules(t_mod):
    txt = t_mod._new_name_prompt("a cash-flow forecaster", {"cashfwd", "dotfiles"},
                                 ["agenthangar", "chrisooob"])
    assert '"a cash-flow forecaster"' in txt
    assert "cashfwd, dotfiles" in txt
    assert "default agenthangar): agenthangar, chrisooob" in txt
    assert "JSON array" in txt and str(t_mod._NEW_NAME_MAX) in txt
    # the conventions are the model's to know: no GitHub rule is spelled out
    assert "github.io" not in txt and ".github" not in txt
    bare = t_mod._new_name_prompt("x", set())
    assert "taken" not in bare and "owners" not in bare


def test_new_parse_names_tolerates_prose_validates_and_dedupes(t_mod):
    reply = ('Here you go:\n```json\n[{"name": "CashFwd", "why": "short"}, '
             '{"name": "cashfwd", "why": "dup"}, {"name": "-bad", "why": "invalid"}, '
             '{"name": "taken-one"}, "bare-string", {"why": "no name"}]\n```')
    assert t_mod._new_parse_names(reply, taken={"taken-one"}) == [
        ("cashfwd", "short", None), ("bare-string", "", None)]
    assert t_mod._new_parse_names("no array here") == []
    assert t_mod._new_parse_names("[not json") == []
    assert t_mod._new_parse_names("[not json]") == []
    assert t_mod._new_parse_names("") == []
    many = json.dumps([{"name": f"n{i}"} for i in range(20)])
    assert len(t_mod._new_parse_names(many)) == t_mod._NEW_NAME_MAX


def test_new_parse_names_keeps_only_known_tied_owners(t_mod):
    reply = json.dumps([{"name": "chrisooob.github.io", "why": "pages", "owner": "ChrisOoob"},
                        {"name": ".github", "why": "org health", "owner": "agenthangar"},
                        {"name": "plain", "why": "", "owner": "someone-else"},
                        {"name": "none", "why": ""}])
    assert t_mod._new_parse_names(reply, owners=["agenthangar", "chrisooob"]) == [
        ("chrisooob.github.io", "pages", "chrisooob"), (".github", "org health", "agenthangar"),
        ("plain", "", None), ("none", "", None)]


def test_new_owner_default_and_warnings_follow_the_tied_owner(t_mod):
    rows = [("agenthangar", "agenthangar"), ("chrisooob", "chrisooob (you)")]
    assert t_mod._new_owner_default("ChrisOoob", rows) == 1
    assert t_mod._new_owner_default("elsewhere", rows) == 0
    assert t_mod._new_owner_default(None, rows) == 0
    assert t_mod._new_owner_warnings("plain", "agenthangar", None) == []
    assert t_mod._new_owner_warnings("chrisooob.github.io", "ChrisOoob", "chrisooob") == []
    w = t_mod._new_owner_warnings("chrisooob.github.io", "agenthangar", "chrisooob")
    assert len(w) == 1 and "'chrisooob'" in w[0] and "'agenthangar'" in w[0]


def test_new_taken_names_is_basenames_plus_code_entries(t_mod):
    assert t_mod._new_taken_names(["/Users/x/code/dotfiles", "/Users/x/work/api/", ""],
                                  ["api", "hive", ""]) == {"dotfiles", "api", "hive"}


def test_new_state_summary(t_mod):
    assert t_mod._new_state_summary(_new_state()) == "new"
    assert t_mod._new_state_summary(_new_state(exists=True)) == "exists (empty)"
    assert "NOT a git repo" in t_mod._new_state_summary(_new_state(exists=True, nonempty=True))
    s = _new_state(exists=True, nonempty=True, is_repo=True, has_commits=True, branch="main",
                   origin_url="git@github.com:o/n.git")
    assert t_mod._new_state_summary(s) == "exists — repo on main, origin o/n"
    s = _new_state(exists=True, nonempty=True, is_repo=True, branch="main")
    assert t_mod._new_state_summary(s) == "exists — repo on main, no origin, no commits"


def _plan(t_mod, state, gh, register=("run", None), hosts=(("mini", "u@mini"),), owner="agenthangar",
          private=True, alias="x"):
    return t_mod._new_plan("x", alias, owner, private, "/Users/me/code/x", state, gh, register, list(hosts))


def _by(steps):
    return {s["step"]: s for s in steps}


def test_new_plan_fresh_repo(t_mod, monkeypatch):
    monkeypatch.setattr(t_mod, "HOME", "/Users/me")
    steps = _plan(t_mod, _new_state(), ("absent", None))
    assert [s["step"] for s in steps] == ["init", "commit", "remote", "push", "settings", "register", "host:mini"]
    b = _by(steps)
    assert b["init"]["do"] == "run" and b["init"]["cmd"] == ["git", "init", "-q", "-b", "main", "/Users/me/code/x"]
    assert b["commit"]["cmd"][-3:] == ["--allow-empty", "-m", "init: x"]
    assert b["remote"]["cmd"] == ["gh", "repo", "create", "agenthangar/x", "--private", "--source",
                                  "/Users/me/code/x", "--remote", "origin", "--push"]
    assert b["push"]["do"] == "skip" and "gh repo create" in b["push"]["label"]
    assert b["settings"]["cmd"] == ["gh", "repo", "edit", "agenthangar/x", "--enable-auto-merge",
                                    "--delete-branch-on-merge", "--enable-squash-merge",
                                    "--enable-merge-commit=false", "--enable-rebase-merge=false"]
    assert b["register"]["do"] == "run" and 'DEV_REPOS[x]="$HOME/code/x"' in b["register"]["label"]
    assert b["host:mini"]["cmd"] == t_mod._new_ssh_argv("u@mini", "git@github.com:agenthangar/x.git", "x", "x")
    assert not any(s["do"] == "error" for s in steps)


def test_new_plan_public_and_no_hosts(t_mod):
    b = _by(_plan(t_mod, _new_state(), ("absent", None), private=False, hosts=()))
    assert "--public" in b["remote"]["cmd"] and not any(k.startswith("host:") for k in b)


def test_new_plan_resumes_local_repo_without_remote(t_mod):
    st = _new_state(exists=True, nonempty=True, is_repo=True, has_commits=True, branch="main")
    b = _by(_plan(t_mod, st, ("absent", None)))
    assert b["init"]["do"] == "skip" and b["commit"]["do"] == "skip"
    assert b["remote"]["cmd"][:3] == ["gh", "repo", "create"]


def test_new_plan_repo_on_github_only_adds_origin_and_pushes(t_mod):
    st = _new_state(exists=True, nonempty=True, is_repo=True, has_commits=True, branch="main")
    b = _by(_plan(t_mod, st, ("present", _GH_OK)))
    assert b["remote"]["cmd"] == ["git", "-C", "/Users/me/code/x", "remote", "add", "origin",
                                  "git@github.com:agenthangar/x.git"]
    assert b["push"]["do"] == "run" and b["push"]["cmd"][-3:] == ["-u", "origin", "main"]
    assert b["settings"]["do"] == "skip"


def test_new_plan_resumes_after_failed_push(t_mod):
    st = _new_state(exists=True, nonempty=True, is_repo=True, has_commits=True, branch="main",
                    origin_url="git@github.com:agenthangar/x.git", has_origin_main=False)
    b = _by(_plan(t_mod, st, ("present", _GH_OK)))
    assert b["remote"]["do"] == "skip" and b["push"]["do"] == "run"


def test_new_plan_origin_mismatch_warns_and_retargets(t_mod):
    st = _new_state(exists=True, nonempty=True, is_repo=True, has_commits=True, branch="main",
                    origin_url="https://github.com/chrisooob/x", has_origin_main=True)
    b = _by(_plan(t_mod, st, ("present", _GH_HIVE)))
    assert "chrisooob/x" in b["remote"]["warn"]
    assert b["settings"]["cmd"][3] == "chrisooob/x"
    assert b["host:mini"]["url"] == "https://github.com/chrisooob/x"


def test_new_plan_all_done_is_all_skips(t_mod):
    st = _new_state(exists=True, nonempty=True, is_repo=True, has_commits=True, branch="main",
                    origin_url="git@github.com:agenthangar/x.git", has_origin_main=True)
    steps = _plan(t_mod, st, ("present", _GH_OK), register=("skip", "already"), hosts=())
    assert all(s["do"] == "skip" for s in steps)


def test_new_plan_settings_drift_and_visibility_warn(t_mod):
    st = _new_state(exists=True, nonempty=True, is_repo=True, has_commits=True, branch="main",
                    origin_url="git@github.com:agenthangar/x.git", has_origin_main=True)
    b = _by(_plan(t_mod, st, ("present", {**_GH_HIVE, "private": False})))
    assert b["settings"]["do"] == "run" and "exists as public" in b["settings"]["warn"]


def test_new_plan_error_cases(t_mod):
    b = _by(_plan(t_mod, _new_state(exists=True, nonempty=True), ("absent", None)))
    assert b["init"]["do"] == "error" and "not a git repo" in b["init"]["label"]
    st = _new_state(exists=True, nonempty=True, is_repo=True, has_commits=True, branch="master")
    assert "expected main" in _by(_plan(t_mod, st, ("absent", None)))["init"]["label"]
    st = _new_state(exists=True, nonempty=True, is_repo=True, branch="master")
    b = _by(_plan(t_mod, st, ("absent", None)))
    assert b["init"]["do"] == "run" and b["init"]["cmd"][-2:] == ["HEAD", "refs/heads/main"]
    b = _by(_plan(t_mod, _new_state(), ("error", "gh is not logged in")))
    assert b["remote"]["do"] == "error" and b["settings"]["do"] == "error" and "push" not in b
    st = _new_state(exists=True, nonempty=True, is_repo=True, has_commits=True, branch="main",
                    origin_url="git@gitlab.com:o/x.git")
    assert "not a github.com" in _by(_plan(t_mod, st, ("absent", None)))["remote"]["label"]
    b = _by(_plan(t_mod, _new_state(), ("absent", None), register=("error", "alias taken")))
    assert b["register"]["do"] == "error" and b["register"]["label"] == "alias taken"


def test_new_ssh_argv_two_quoting_layers(t_mod):
    argv = t_mod._new_ssh_argv("u@mini", "git@github.com:o/x.git", "x", "it's")
    assert argv[:5] == ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes"]
    assert argv[5] == "u@mini"
    inner = t_mod.shlex.split(argv[6])
    assert inner[:2] == ["zsh", "-lic"]
    assert t_mod.shlex.split(inner[2]) == ["t", "new-land", "git@github.com:o/x.git", "x", "it's"]


def test_new_host_outcome_classes(t_mod):
    assert t_mod._new_host_outcome(255, "", "ssh: connect timed out") == ("unreachable", "")
    assert t_mod._new_host_outcome(124, "", "timed out") == ("unreachable", "")
    assert t_mod._new_host_outcome(2, "", "t: error: argument <verb>: invalid choice: 'new-land'") == ("stale", "")
    assert t_mod._new_host_outcome(2, "", "usage: t new-land [-h] url name alias") == ("failed", "usage: t new-land [-h] url name alias")
    assert t_mod._new_host_outcome(127, "", "zsh:1: command not found: t") == ("missing", "")
    assert t_mod._new_host_outcome(0, "noise\nnew-land: cloned + registered\n", "") == ("ok", "cloned + registered")
    assert t_mod._new_host_outcome(0, "", "") == ("ok", "done")
    st, detail = t_mod._new_host_outcome(1, "", "a\n\nb\nc\nd\n")
    assert st == "failed" and detail == "b / c / d"
    assert t_mod._new_host_outcome(3, "", "") == ("failed", "rc 3")


def test_new_host_line_texts(t_mod):
    assert t_mod._new_host_line("mini", "ok", "cloned + registered", "x") == "✓ mini: cloned + registered"
    assert "re-run `t new x`" in t_mod._new_host_line("mini", "unreachable", "", "x")
    assert "run `dots` there" in t_mod._new_host_line("mini", "stale", "", "x")
    assert "install.sh" in t_mod._new_host_line("mini", "missing", "", "x")
    assert t_mod._new_host_line("mini", "failed", "boom", "x") == "⚠ mini: failed — boom"


def test_new_land_plan_cases(t_mod, monkeypatch):
    monkeypatch.setattr(t_mod, "HOME", "/Users/me")
    url, path = "git@github.com:o/x.git", "/Users/me/code/x"
    b = _by(t_mod._new_land_plan("x", "x", url, path, _new_state(), ("run", None)))
    assert b["clone"]["cmd"] == ["git", "clone", "-q", url, path] and b["register"]["do"] == "run"
    assert 'DEV_REPOS[x]="$HOME/code/x"' == b["register"]["label"]
    st = _new_state(exists=True, nonempty=True, is_repo=True, origin_url="https://github.com/o/x")
    assert _by(t_mod._new_land_plan("x", "x", url, path, st, ("skip", "already")))["clone"]["do"] == "skip"
    st = _new_state(exists=True, nonempty=True, is_repo=True, origin_url="git@github.com:other/x.git")
    assert "expected o/x" in _by(t_mod._new_land_plan("x", "x", url, path, st, ("run", None)))["clone"]["label"]
    st = _new_state(exists=True, nonempty=True, is_repo=True)
    assert _by(t_mod._new_land_plan("x", "x", url, path, st, ("run", None)))["clone"]["do"] == "error"
    st = _new_state(exists=True, nonempty=True)
    assert _by(t_mod._new_land_plan("x", "x", url, path, st, ("run", None)))["clone"]["do"] == "error"
    st = _new_state(exists=True)
    assert _by(t_mod._new_land_plan("x", "x", url, path, st, ("run", None)))["clone"]["do"] == "run"


def test_new_land_summary_variants(t_mod):
    mk = lambda c, r: [{"step": "clone", "do": c}, {"step": "register", "do": r}]
    assert t_mod._new_land_summary(mk("run", "run")) == "cloned + registered"
    assert t_mod._new_land_summary(mk("skip", "skip")) == "already set up"
    assert t_mod._new_land_summary(mk("skip", "run")) == "clone present, registered"
    assert t_mod._new_land_summary(mk("run", "skip")) == "cloned (alias already registered)"


def test_new_render_prefixes(t_mod):
    steps = [{"step": "init", "do": "run", "label": "git init", "warn": None},
             {"step": "commit", "do": "skip", "label": "already", "warn": None},
             {"step": "remote", "do": "error", "label": "nope", "warn": "careful"},
             {"step": "host:mini", "do": "run", "label": "mini", "warn": None, "host": "mini"},
             {"step": "host:oc", "do": "run", "label": "oc", "warn": None, "host": "oc"}]
    lines = t_mod._new_render(steps, t_mod.Style())
    assert lines == ["+ git init", "= already", "✗ nope", "  ⚠ careful", "→ mini, oc: clone + register"]


def test_setup_hosts_trailer(t_mod):
    assert t_mod._setup_hosts_trailer([]) is None
    assert t_mod._setup_hosts_trailer([("mini", "u@m"), ("oc", "u@o")]) == \
        "then clone + register the picked repos on: mini, oc"


def test_setup_block_tool_header(t_mod, monkeypatch):
    monkeypatch.setattr(t_mod, "HOME", "/Users/me")
    block = t_mod._setup_block({"x": "/Users/me/code/x"}, {}, None, "2026-08-24", tool="t new")
    assert block == "# ── added by `t new` (2026-08-24) ──\nDEV_REPOS[x]=\"$HOME/code/x\"\n"
def test_port_is_live_sees_an_ipv6_only_listener(t_mod):
    # Vite without --host binds ONLY [::1] on modern macOS/Node; a 127.0.0.1 probe
    # called ff-12's live :5212 dead and the bar fell through to the shared :5173.
    import socket
    if not socket.has_ipv6:
        return
    srv = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        srv.bind(("::1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        assert t_mod._port_is_live(port) is True
    finally:
        srv.close()
    assert t_mod._port_is_live(port) is False


# ─── mcp: transcript extraction ────────────────────────────────────────────────

def _jl(d):
    """One compact transcript line, the way Claude writes them ("type":"user", no spaces)."""
    return (json.dumps(d, separators=(",", ":")) + "\n").encode()


def _user(text, ts="2026-08-23T06:37:38.765Z", cwd="/wt/financial-forecast/17",
          sid="dd81442d-bf47-418c-a0f2-4956371f1181", branch="dev/financial-forecast-17", **extra):
    d = {"type": "user", "message": {"role": "user", "content": text}, "timestamp": ts,
         "cwd": cwd, "sessionId": sid, "gitBranch": branch, "isSidechain": False}
    d.update(extra)
    return d


def test_tx_user_text_str(t_mod):
    assert t_mod._tx_user_text({"type": "user", "message": {"content": "  fix  the ledger "}}) == "fix the ledger"


def test_tx_user_text_blocks_join_text_only(t_mod):
    rec = {"type": "user", "message": {"content": [
        {"type": "text", "text": "extra card payment [Image #1]"},
        {"type": "image", "source": {"data": "..."}}]}}
    assert t_mod._tx_user_text(rec) == "extra card payment [Image #1]"


def test_tx_user_text_drops_tool_results_and_injections(t_mod):
    assert t_mod._tx_user_text({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "x", "content": "big output"}]}}) is None
    assert t_mod._tx_user_text({"type": "user", "message": {"content": "<command-name>/todo</command-name>"}}) is None
    assert t_mod._tx_user_text({"type": "user", "isMeta": True, "message": {"content": "[Image: source]"}}) is None
    assert t_mod._tx_user_text({"type": "user", "isSidechain": True, "message": {"content": "subagent turn"}}) is None
    assert t_mod._tx_user_text({"type": "assistant", "message": {"content": "hi"}}) is None
    assert t_mod._tx_user_text({"type": "user", "message": {"content": None}}) is None
    assert t_mod._tx_user_text({"type": "user"}) is None


def test_tx_scan_fills_every_field(t_mod, monkeypatch):
    monkeypatch.setattr(t_mod, "HOME", "/Users/me")
    st = t_mod._index_empty(1)
    data = b"".join([
        _jl({"type": "last-prompt", "sessionId": "sid-1"}),
        _jl(_user("first ask", ts="2026-08-01T10:00:00Z", sid="sid-1", branch="dev/ff-1")),
        _jl({"type": "ai-title", "aiTitle": "AI title", "sessionId": "sid-1"}),
        _jl({"type": "assistant", "timestamp": "2026-08-01T10:00:05Z", "message": {"content": [
            {"type": "thinking", "thinking": "..."}, {"type": "text", "text": "I  will  look"}]}}),
        _jl({"type": "pr-link", "prUrl": "https://github.com/o/r/pull/12"}),
        _jl(_user("see https://github.com/o/r/pull/34 and /Users/me/.claude/plans/x-y.md",
                  ts="2026-08-02T10:00:00Z", sid="sid-1", branch="dev/ff-1b")),
        _jl({"type": "custom-title", "customTitle": "Custom", "sessionId": "sid-1"}),
        _jl({"type": "assistant", "isSidechain": True, "message": {"content": [
            {"type": "text", "text": "subagent text must not win"}]}}),
        b"not json at all\n",
        _jl(["a list record is ignored"]),
    ])
    t_mod._tx_scan(st, data)
    assert st["sid"] == "sid-1" and st["cwd"] == "/wt/financial-forecast/17"
    assert st["branch"] == "dev/ff-1b"            # last gitBranch wins
    assert st["at"] == "AI title" and st["ct"] == "Custom"
    assert st["pr"] == "github.com/o/r/pull/34"   # last PR mention wins, pr-link included
    assert st["plan"] == "/Users/me/.claude/plans/x-y.md"
    assert st["n_prompts"] == 2 and [p["text"] for p in st["prompts"]][0] == "first ask"
    assert st["prompts"][0]["ts"] == t_mod._iso_epoch("2026-08-01T10:00:00Z")
    assert st["first_ts"] == t_mod._iso_epoch("2026-08-01T10:00:00Z")
    assert st["last_ts"] == t_mod._iso_epoch("2026-08-02T10:00:00Z")
    assert st["last_asst"] == "I will look"


def test_tx_scan_truncates_and_trims(t_mod):
    st = t_mod._index_empty(1)
    t_mod._tx_scan(st, b"".join(_jl(_user("p%03d " % i + "x" * 500)) for i in range(90)))
    assert st["n_prompts"] == 90
    assert len(st["prompts"]) == t_mod._INDEX_HEAD + t_mod._INDEX_TAIL
    assert st["prompts"][0]["text"].startswith("p000")
    assert st["prompts"][t_mod._INDEX_HEAD]["text"].startswith("p018")   # first of the tail
    assert st["prompts"][-1]["text"].startswith("p089")
    assert all(len(p["text"]) <= t_mod._INDEX_TEXT for p in st["prompts"])


def test_index_trim_leaves_short_lists_alone(t_mod):
    ps = [{"text": str(i)} for i in range(5)]
    assert t_mod._index_trim(ps, head=2, tail=2) is ps and len(ps) == 4
    ps = [{"text": "a"}]
    assert t_mod._index_trim(ps, head=2, tail=2) == [{"text": "a"}]


def test_iso_epoch(t_mod):
    assert t_mod._iso_epoch("2026-08-23T06:37:38.765Z") == 1787467058
    assert t_mod._iso_epoch(None) is None and t_mod._iso_epoch("garbage") is None


# ─── mcp: index cache ──────────────────────────────────────────────────────────

def _tx_file(path, *recs):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        for r in recs:
            fh.write(_jl(r))
    return str(path)


def test_index_update_cold_then_incremental(t_mod, tmp_path):
    cache = tmp_path / "index"
    tx = _tx_file(tmp_path / "p" / "s.jsonl", _user("one"), _user("two"))
    rec = t_mod._index_update(tx, str(cache))
    assert rec["n_prompts"] == 2 and rec["off"] == os.path.getsize(tx) and rec["path"] == tx
    assert sorted(os.listdir(cache)) == [t_mod._index_key(tx)]        # tmp cleaned up
    with open(tx, "ab") as fh:
        fh.write(_jl(_user("three")))
        fh.write(b'{"type":"user","message":{"content":"half writ')       # no newline yet
    rec = t_mod._index_update(tx, str(cache))
    assert rec["n_prompts"] == 3                                        # the whole line only
    assert rec["off"] == os.path.getsize(tx) - len(b'{"type":"user","message":{"content":"half writ')
    with open(tx, "ab") as fh:
        fh.write(b'ten"}}\n')
    rec = t_mod._index_update(tx, str(cache))
    assert rec["n_prompts"] == 4 and rec["prompts"][-1]["text"] == "half written"
    assert rec["off"] == os.path.getsize(tx)
    # nothing appended → no rescan, same answer
    assert t_mod._index_update(tx, str(cache))["n_prompts"] == 4


def test_index_update_resets_on_inode_corruption_or_schema(t_mod, tmp_path, monkeypatch):
    cache = tmp_path / "index"
    tx = _tx_file(tmp_path / "p" / "s.jsonl", _user("one"), _user("two"))
    assert t_mod._index_update(tx, str(cache))["n_prompts"] == 2
    cpath = cache / t_mod._index_key(tx)
    # a NEW file at the same path (csync's rsync) → different inode → full rescan
    _tx_file(tmp_path / "p" / "s.new", _user("only"))
    os.replace(tmp_path / "p" / "s.new", tx)
    assert t_mod._index_update(tx, str(cache))["n_prompts"] == 1
    # corrupt cache → rescan
    cpath.write_text("{not json")
    assert t_mod._index_update(tx, str(cache))["n_prompts"] == 1
    # older schema → rescan
    st = json.loads(cpath.read_text()); st["v"] = 0; st["n_prompts"] = 99
    cpath.write_text(json.dumps(st))
    assert t_mod._index_update(tx, str(cache))["n_prompts"] == 1
    # cache claims more bytes than exist (truncation) → rescan
    st = json.loads(cpath.read_text()); st["off"] = 10 ** 9; st["n_prompts"] = 99
    cpath.write_text(json.dumps(st))
    assert t_mod._index_update(tx, str(cache))["n_prompts"] == 1
    # unreadable transcript → None
    assert t_mod._index_update(str(tmp_path / "nope.jsonl"), str(cache)) is None


def test_index_update_survives_unwritable_cache(t_mod, tmp_path):
    tx = _tx_file(tmp_path / "p" / "s.jsonl", _user("one"))
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x")
    rec = t_mod._index_update(tx, str(blocker / "index"))
    assert rec["n_prompts"] == 1


def test_index_save_swallows_oserror(t_mod, tmp_path):
    blocker = tmp_path / "f"
    blocker.write_text("x")
    t_mod._index_save(str(blocker / "sub" / "key"), {"v": 1})   # must not raise


def test_index_all_one_level_only(t_mod, tmp_path):
    proj = tmp_path / "projects"
    _tx_file(proj / "-wt-ff-17" / "a.jsonl", _user("top"))
    _tx_file(proj / "-wt-ff-17" / "a" / "subagents" / "agent-1.jsonl", _user("sub"))
    recs = t_mod._index_all(str(proj), str(tmp_path / "index"))
    assert [r["prompts"][0]["text"] for r in recs] == ["top"]


def test_index_dedupe_keeps_larger_copy(t_mod):
    a = {"sid": "s", "cwd": "/wt/ff/5", "size": 10}
    b = {"sid": "s", "cwd": "/wt/ff/7", "size": 20}
    c = {"sid": None, "cwd": "/x", "size": 1}
    out = t_mod._index_dedupe([a, b, c])
    keep = [r for r in out if r.get("sid") == "s"][0]
    assert keep["cwd"] == "/wt/ff/7" and keep["also_at"] == ["/wt/ff/5"]
    assert c in out and len(out) == 2
    # order-independent
    keep = [r for r in t_mod._index_dedupe([b, a]) if r.get("sid") == "s"][0]
    assert keep["cwd"] == "/wt/ff/7" and keep["also_at"] == ["/wt/ff/5"]


# ─── mcp: enrichment ───────────────────────────────────────────────────────────

def test_pr_info_reads_cache_only(t_mod, tmp_path):
    (tmp_path / "o#r#12").write_text("MERGED")
    (tmp_path / "o#r#13").write_text("weird")
    assert t_mod._pr_info("github.com/o/r/pull/12", str(tmp_path)) == {
        "url": "https://github.com/o/r/pull/12", "repo": "o/r", "number": 12, "state": "merged"}
    assert t_mod._pr_info("github.com/o/r/pull/13", str(tmp_path))["state"] == "?"
    assert t_mod._pr_info("github.com/o/r/pull/14", str(tmp_path))["state"] == "?"
    assert t_mod._pr_info(None, str(tmp_path)) is None
    assert t_mod._pr_info("not a url", str(tmp_path)) is None


def test_opened_origin_activity(t_mod, tmp_path):
    (tmp_path / "opened").mkdir()
    (tmp_path / "opened" / "sid").write_text("")
    assert t_mod._opened_stamp("sid", str(tmp_path)) > 0
    assert t_mod._opened_stamp("nope", str(tmp_path)) == 0
    tx = tmp_path / "s.jsonl"
    tx.write_text("")
    assert t_mod._origin_of(str(tx)) is None
    (tmp_path / "s.origin").write_text("mini\n")
    assert t_mod._origin_of(str(tx)) == "mini"
    assert t_mod._activity({"last_ts": 100, "mtime": 200.5}, 150) == 200
    assert t_mod._activity({"last_ts": None, "mtime": 10}, 30) == 30


def test_ago_and_fmt(t_mod):
    assert t_mod._ago(1000, 1010) == "just now"
    assert t_mod._ago(1000, 1000 + 600) == "10m ago"
    assert t_mod._ago(1000, 1000 + 5 * 3600) == "5h ago"
    assert t_mod._ago(1000, 1000 + 3 * 86400) == "3d ago"
    assert t_mod._fmt_ts(None) is None and t_mod._fmt_short(None) is None
    assert t_mod._fmt_ts(1787467058) == time.strftime("%Y-%m-%d %H:%M", time.localtime(1787467058))


def test_rec_title_order(t_mod):
    assert t_mod._rec_title({"ct": "c", "at": "a", "prompts": [{"text": "p"}]}) == "c"
    assert t_mod._rec_title({"at": "a", "prompts": [{"text": "p"}]}) == "a"
    assert t_mod._rec_title({"prompts": [{"text": "p" * 100}]}) == "p" * 80
    assert t_mod._rec_title({}) is None


def _mcp_cfg(t_mod, tmp_path, monkeypatch, worktree_root="/wt"):
    return _config_with(t_mod, tmp_path, monkeypatch,
                        {"ff": "/code/financial-forecast", "dot": "/code/dotfiles",
                         "dotfiles": "/code/dotfiles"}, worktree_root=worktree_root)


def _rec(sid="aaaa1111-0000-0000-0000-000000000000", cwd="/wt/financial-forecast/17",
         prompts=("first ask", "later ask"), **kw):
    r = {"sid": sid, "cwd": cwd, "branch": None, "ct": None, "at": None, "pr": None,
         "plan": None, "first_ts": 1000, "last_ts": 2000, "mtime": 2000.0, "size": 10,
         "n_prompts": len(prompts), "prompts": [{"ts": 1000 + i, "text": p} for i, p in enumerate(prompts)],
         "last_asst": None, "path": "/nonexistent/%s.jsonl" % sid}
    r.update(kw)
    return r


def test_session_row_reaped_worktree_and_defaults(t_mod, tmp_path, monkeypatch):
    cfg = _mcp_cfg(t_mod, tmp_path, monkeypatch)
    row = t_mod._session_row(cfg, _rec(), {}, now=2000 + 7200, local_host="here")
    assert row["repo"] == "ff" and row["slot"] == "ff-17"
    assert row["worktree"] == "/wt/financial-forecast/17" and row["worktree_exists"] is False
    assert row["branch"] == "dev/financial-forecast-17"        # derived when the transcript has none
    assert row["state"] == "dead" and row["live"] is False and row["context"] is None
    assert row["ago"] == "2h ago" and row["title"] == "first ask"
    assert row["first_prompts"] == ["first ask", "later ask"] and row["last_prompts"] == []
    assert row["pr"] is None and row["this_session"] is False


def test_session_row_live_pr_origin_and_this(t_mod, tmp_path, monkeypatch):
    cfg = _mcp_cfg(t_mod, tmp_path, monkeypatch, worktree_root=str(tmp_path / "wt"))
    wt = tmp_path / "wt" / "financial-forecast" / "17"
    wt.mkdir(parents=True)
    cache = tmp_path / "cache"
    (cache / "pr").mkdir(parents=True)
    (cache / "pr" / "o#r#5").write_text("OPEN")
    (cache / "opened").mkdir()
    tx = tmp_path / "s.jsonl"
    tx.write_text("")
    (tmp_path / "s.origin").write_text("mini")
    rec = _rec(cwd=str(wt), branch="dev/x", pr="github.com/o/r/pull/5", path=str(tx),
               prompts=("a", "b", "c", "d", "e"), ct="Custom")
    live = {rec["sid"]: {"slot": "ff-17", "state": "attached", "context": "active", "cwd": str(wt)}}
    row = t_mod._session_row(cfg, rec, live, now=3000, cache_root=str(cache),
                             this_sid=rec["sid"], local_host="here")
    assert row["live"] and row["state"] == "attached" and row["context"] == "active"
    assert row["worktree_exists"] and row["branch"] == "dev/x" and row["title"] == "Custom"
    assert row["pr"]["state"] == "open" and row["pr"]["number"] == 5
    assert row["origin"] == "mini" and row["this_session"]
    assert row["first_prompts"] == ["a", "b", "c"] and row["last_prompts"] == ["c", "d", "e"]
    # the origin stamp of THIS host is not an origin
    (tmp_path / "s.origin").write_text("here")
    assert t_mod._session_row(cfg, rec, live, 3000, local_host="here")["origin"] is None


def test_session_row_outside_any_repo(t_mod, tmp_path, monkeypatch):
    cfg = _mcp_cfg(t_mod, tmp_path, monkeypatch)
    row = t_mod._session_row(cfg, _rec(cwd="/somewhere/else"), {}, now=5000)
    assert row["repo"] is None and row["slot"] is None and row["worktree"] is None
    assert row["branch"] is None


# ─── mcp: liveness ─────────────────────────────────────────────────────────────

def test_live_index_joins_by_pid_ancestry(t_mod, tmp_path, monkeypatch):
    cfg = _mcp_cfg(t_mod, tmp_path, monkeypatch)
    tmux_rows = [("dev-ff-17", "/wt/financial-forecast/17", "attached"),
                 ("dev-ff-9", "/wt/financial-forecast/9", "detached"),
                 ("scratch", "/tmp", "detached")]
    panes = [("dev-ff-17", "100"), ("dev-ff-9", "200"), ("scratch", "900")]
    parents = {101: 100, 102: 101, 300: 1, 105: 100, 901: 900}
    registry = {102: ("sidA", "/wt/financial-forecast/17"),          # grandchild of pane 100
                300: ("sidB", "/code/dotfiles"),                       # no pane above it
                105: ("sidC", "/wt/financial-forecast/17"),           # second claude in the pane
                901: ("sidD", "/tmp")}                                 # in a non-dev session
    out, idle = t_mod._live_index(tmux_rows, panes, parents, registry, cfg)
    assert out["sidA"] == {"slot": "ff-17", "cwd": "/wt/financial-forecast/17", "state": "attached",
                           "kind": "tmux", "context": "active"}
    assert out["sidB"]["kind"] == "foreground" and out["sidB"]["slot"] == "dotfiles:sidB"
    assert out["sidC"]["kind"] == "foreground"                       # the older pid claimed the pane
    assert out["sidD"]["kind"] == "foreground" and out["sidD"]["slot"] == "tmp:sidD"
    assert idle == [{"slot": "ff-9", "cwd": "/wt/financial-forecast/9", "state": "detached",
                     "context": "idle"}]


def test_live_index_empty_inputs(t_mod, tmp_path, monkeypatch):
    cfg = _mcp_cfg(t_mod, tmp_path, monkeypatch)
    assert t_mod._live_index([], [], {}, {}, cfg) == ({}, [])
    out, idle = t_mod._live_index([], [], {}, {5: ("", "/x"), 6: ("sid", "")}, cfg)
    assert list(out) == ["sid"] and out["sid"]["slot"] == "?:sid"


# ─── mcp: search ───────────────────────────────────────────────────────────────

def test_kw_terms(t_mod):
    assert t_mod._kw_terms("Which session was working on the #accounts page redesign?") == \
        ["#account", "page", "redesign"]
    assert t_mod._kw_terms("amex extra payments: edit, reschedule") == \
        ["amex", "extra", "payment", "edit", "reschedule"]
    assert t_mod._kw_terms("the of a") == [] and t_mod._kw_terms("") == [] and t_mod._kw_terms(None) == []
    assert t_mod._kw_terms("class classes boss") == ["class", "classe", "boss"]   # crude on purpose
    assert t_mod._kw_terms("Ledger ledger LEDGER") == ["ledger"]


def test_kw_score_weights_head(t_mod):
    rec = {"ct": "Redesign accounts", "prompts": [{"text": "accounts page is slow"}]}
    assert t_mod._kw_score(rec, ["account", "redesign"]) == 1 + 4 + 4
    assert t_mod._kw_score({"prompts": []}, ["x"]) == 0
    assert t_mod._kw_score({"at": "x marks", "prompts": [{"text": "no hit"}]}, ["x"]) == 4


def test_kw_excerpts_windows_and_orders(t_mod):
    ts = 1787467058
    prompts = [{"ts": None, "text": "nothing relevant here"},
               {"ts": ts, "text": "z" * 300 + " the accounts page redesign " + "z" * 300},
               {"ts": None, "text": "accounts accounts accounts"},
               {"ts": None, "text": "accounts once more"}]
    out = t_mod._kw_excerpts(prompts, ["account", "redesign"], n=3, width=60)
    assert len(out) == 3
    assert out[0] == "accounts accounts accounts"                     # most hits first
    assert out[1].startswith(time.strftime("%m-%d %H:%M", time.localtime(ts)) + " · …")
    assert "accounts page redesign" in out[1] and out[1].endswith("…") and len(out[1]) < 90
    assert out[2] == "accounts once more"                             # tie → latest first
    assert t_mod._kw_excerpts(prompts, ["nomatch"]) == []


def _find_fixture(t_mod, tmp_path, monkeypatch):
    cfg = _mcp_cfg(t_mod, tmp_path, monkeypatch)
    now = 10 ** 9
    recs = [
        _rec(sid="stub0000", cwd="/wt/financial-forecast/3", prompts=(), n_prompts=0),
        _rec(sid="ff050000", cwd="/wt/financial-forecast/5", at="Integrate payments",
             prompts=("need to edit payment title", "payment date picker"),
             last_ts=now - 100 * 86400, mtime=now - 100 * 86400),
        _rec(sid="dot30000", cwd="/wt/dotfiles/3", prompts=("payment docs",),
             last_ts=now - 10, mtime=now - 10),
        _rec(sid="ff170000", cwd="/wt/financial-forecast/17", prompts=("accounts ledger",),
             last_ts=now - 20, mtime=now - 20),
    ]
    return cfg, recs, now


def test_find_sessions_ranks_filters_and_windows(t_mod, tmp_path, monkeypatch):
    cfg, recs, now = _find_fixture(t_mod, tmp_path, monkeypatch)
    out = t_mod._find_sessions(recs, ["payment"], {}, cfg, days=60, now=now)
    assert out["scanned"] == 3 and out["matched"] == 1                # the stub is never scanned
    assert [r["sid"] for r in out["results"]] == ["dot30000"]           # ff-5 is outside the window
    out = t_mod._find_sessions(recs, ["payment"], {}, cfg, days=0, now=now)
    assert [r["sid"] for r in out["results"]] == ["ff050000", "dot30000"]   # score, then recency
    assert out["results"][0]["score"] == 2 + 4 and out["results"][0]["excerpts"]
    out = t_mod._find_sessions(recs, ["payment"], {}, cfg, days=0, repo="dot", now=now)
    assert [r["sid"] for r in out["results"]] == ["dot30000"]           # dot == dotfiles by path
    out = t_mod._find_sessions(recs, ["payment"], {}, cfg, days=0, limit=1, now=now,
                               this_sid="ff050000")
    assert len(out["results"]) == 1 and out["matched"] == 2 and out["results"][0]["this_session"]
    with pytest.raises(ValueError):
        t_mod._find_sessions(recs, ["x"], {}, cfg, repo="nope", now=now)


def test_find_sessions_clamps_limit_and_days(t_mod, tmp_path, monkeypatch):
    cfg, recs, now = _find_fixture(t_mod, tmp_path, monkeypatch)
    out = t_mod._find_sessions(recs, ["payment"], {}, cfg, days=-5, limit=999, now=now)
    assert out["window_days"] == 0 and len(out["results"]) == 2
    out = t_mod._find_sessions(recs, ["payment"], {}, cfg, days="0", limit="0", now=now)
    assert len(out["results"]) == 1


# ─── mcp: session ref ──────────────────────────────────────────────────────────

def test_session_ref_resolve_forms(t_mod, tmp_path, monkeypatch):
    cfg, recs, now = _find_fixture(t_mod, tmp_path, monkeypatch)
    older = _rec(sid="ff17aaaa", cwd="/wt/financial-forecast/17", prompts=("old",),
                 last_ts=now - 500, mtime=now - 500)
    recs = recs + [older]
    live = {"ff17aaaa": {"slot": "ff-17", "cwd": "/wt/financial-forecast/17", "state": "attached"}}
    r = t_mod._session_ref_resolve
    assert r("ff-17", cfg, recs, live)["sid"] == "ff17aaaa"             # live wins over newer dead
    assert r("dev-ff-17", cfg, recs, live)["sid"] == "ff17aaaa"
    assert r("ff 17", cfg, recs, {})["sid"] == "ff170000"                # dead → newest transcript
    assert r("dotfiles-3", cfg, recs, {})["sid"] == "dot30000"
    assert r("dot-3", cfg, recs, {})["sid"] == "dot30000"
    assert r("ff050000", cfg, recs, {})["sid"] == "ff050000"
    assert r("ff05", cfg, recs, {})["sid"] == "ff050000"                 # unique prefix
    assert r("ff:ff05", cfg, recs, {})["sid"] == "ff050000"              # t ls label form
    # a slot live under a sibling alias / shared tree resolves through the live slot name
    live2 = {"dot30000": {"slot": "dot-3", "cwd": "/code/dotfiles", "state": "attached"}}
    assert r("dotfiles-3", cfg, recs, live2)["sid"] == "dot30000"
    with pytest.raises(ValueError, match="ambiguous"):
        r("ff", cfg, recs, {}) if False else r("ff17", cfg, recs, {})
    with pytest.raises(ValueError, match="no transcript for slot"):
        r("ff-3", cfg, recs, {})                                          # only a stub there
    with pytest.raises(ValueError, match="no session id starting"):
        r("beef", cfg, recs, {})
    with pytest.raises(ValueError, match="aliases: dot, dotfiles, ff"):
        r("what even", cfg, recs, {})
    with pytest.raises(ValueError):
        r("", cfg, recs, {})


# ─── mcp: detail helpers ───────────────────────────────────────────────────────

def test_plan_head_lines(t_mod, tmp_path):
    p = tmp_path / "plan.md"
    p.write_text("# one\ntwo\nthree\n")
    assert t_mod._plan_head_lines(str(p), 2) == ["# one", "two"]
    assert t_mod._plan_head_lines(str(p), 0) == ["# one"]
    assert t_mod._plan_head_lines(str(p), "x") is None
    assert t_mod._plan_head_lines(str(tmp_path / "nope.md")) is None
    assert t_mod._plan_head_lines(None) is None


def test_git_brief_absent_dir(t_mod, tmp_path):
    assert t_mod._git_brief(None) is None
    assert t_mod._git_brief(str(tmp_path / "nope")) is None


def test_slot_sort_key(t_mod):
    assert sorted(["ff-10", "ff-9", "dot-2", "ff:abcd"], key=t_mod._slot_sort_key) == \
        ["dot-2", "ff-9", "ff-10", "ff:abcd"]


# ─── mcp: the tools over a hand-built context ──────────────────────────────────

def _ctx(t_mod, recs, live=None, idle=None, now=10 ** 9, this_sid=None):
    live = live or {}
    return {"recs": recs, "live": live, "idle": idle or [],
            "live_rows": [{"slot": v["slot"]} for v in live.values()] + [{"slot": i["slot"]} for i in idle or []],
            "now": now, "cache_root": None, "this_sid": this_sid}


def test_tool_find_needs_keywords(t_mod, tmp_path, monkeypatch):
    cfg, recs, now = _find_fixture(t_mod, tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="keywords"):
        t_mod._tool_find(cfg, _ctx(t_mod, recs, now=now), {"query": "the of"})
    out = t_mod._tool_find(cfg, _ctx(t_mod, recs, now=now), {"query": "payment", "days": 0})
    assert out["matched"] == 2 and out["query_terms"] == ["payment"]


def test_tool_list_live_idle_and_remote(t_mod, tmp_path, monkeypatch):
    cfg, recs, now = _find_fixture(t_mod, tmp_path, monkeypatch)
    live = {"ff170000": {"slot": "ff-17", "cwd": "/wt/financial-forecast/17", "state": "attached",
                         "context": "active", "kind": "tmux"},
            "brandnew": {"slot": "ff-24", "cwd": "/wt/financial-forecast/24", "state": "detached",
                         "context": "active", "kind": "tmux"}}
    idle = [{"slot": "dot-2", "cwd": "/wt/dotfiles/2", "state": "detached", "context": "idle"}]
    ctx = _ctx(t_mod, recs, live, idle, now, this_sid="ff170000")
    out = t_mod._tool_list(cfg, ctx, {})
    assert out["count"] == 3
    slots = [(r["slot"], r["title"]) for r in out["sessions"]]
    assert slots == [("dot-2", "(no active conversation)"), ("ff-17", "accounts ledger"),
                     ("ff-24", "(no transcript yet)")]
    assert out["sessions"][1]["this_session"] and out["sessions"][1]["url"] is None
    assert [r["slot"] for r in t_mod._tool_list(cfg, ctx, {"repo": "dot"})["sessions"]] == ["dot-2"]
    with pytest.raises(ValueError):
        t_mod._tool_list(cfg, ctx, {"repo": "nope"})
    monkeypatch.setattr(t_mod, "zsh_capture", lambda snippet, stdin=None:
                        "local\tx\t/wt/financial-forecast/17\tff-17\tattached\tactive\tlocal row\n"
                        "mini\t-\t/wt/financial-forecast/3\tff-3\tdetached\tidle\t(idle)\n")
    out = t_mod._tool_list(cfg, ctx, {"all_hosts": True})
    assert out["remote"] == [{"host": "mini", "sid": "", "repo": "ff", "slot": "ff-3",
                              "cwd": "/wt/financial-forecast/3", "live": True, "state": "detached",
                              "context": "idle", "title": "(idle)"}]
    assert out["hosts"] == []


def test_tool_detail_assembles_everything(t_mod, tmp_path, monkeypatch):
    cfg, recs, now = _find_fixture(t_mod, tmp_path, monkeypatch)
    plan = tmp_path / "p.md"
    plan.write_text("# plan\nstep\n")
    recs[3]["plan"] = str(plan)
    recs[3]["last_asst"] = "left off here"
    recs[3]["prompts"] = [{"ts": 1, "text": "p%d" % i} for i in range(15)]
    ctx = _ctx(t_mod, recs, now=now)
    row = t_mod._tool_detail(cfg, ctx, {"session": "ff-17", "plan_lines": 1})
    assert row["sid"] == "ff170000" and row["plan_head"] == ["# plan"]
    assert row["last_assistant"] == "left off here" and row["git"] is None
    assert [p["text"] for p in row["first_prompts"]] == ["p0", "p1", "p2"]
    assert [p["text"] for p in row["last_prompts"]] == ["p%d" % i for i in range(5, 15)]
    assert row["hints"][0] == "t resume ff 17"
    assert any("worktree is gone" in h for h in row["hints"])
    assert row["also_at"] == []
    live = {"ff170000": {"slot": "ff-17", "cwd": "/wt/financial-forecast/17", "state": "attached",
                         "context": "active"}}
    row = t_mod._tool_detail(cfg, _ctx(t_mod, recs, live, now=now), {"session": "ff170000"})
    assert row["hints"][0] == "t open ff 17"
    with pytest.raises(ValueError):
        t_mod._tool_detail(cfg, ctx, {"session": "zzzz"})


# ─── mcp: framing ──────────────────────────────────────────────────────────────

def _handle(t_mod, msg, call=None):
    return t_mod._mcp_handle(msg, t_mod._MCP_TOOLS, call or (lambda n, a: {"ok": n, "args": a}),
                             t_mod._MCP_SERVER_INFO, t_mod._MCP_INSTRUCTIONS)


def test_mcp_handle_initialize_echoes_version_and_instructions(t_mod):
    r = _handle(t_mod, {"jsonrpc": "2.0", "id": 0, "method": "initialize",
                        "params": {"protocolVersion": "2099-01-01"}})
    assert r["id"] == 0 and r["result"]["protocolVersion"] == "2099-01-01"
    assert r["result"]["serverInfo"]["name"] == "sessions"
    assert "UNPROMPTED" in r["result"]["instructions"] and r["result"]["capabilities"] == {"tools": {}}
    r = _handle(t_mod, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r["result"]["protocolVersion"] == t_mod._MCP_PROTOCOL_FALLBACK
    r = t_mod._mcp_handle({"id": 2, "method": "initialize"}, [], lambda n, a: None)
    assert r["result"]["serverInfo"] == t_mod._MCP_SERVER_INFO and "instructions" not in r["result"]


def test_mcp_handle_misc_methods(t_mod):
    assert _handle(t_mod, {"id": 1, "method": "ping"})["result"] == {}
    tools = _handle(t_mod, {"id": 2, "method": "tools/list"})["result"]["tools"]
    assert [t["name"] for t in tools] == ["find_sessions", "list_sessions", "session_detail"]
    assert all(set(t) == {"name", "description", "inputSchema"} for t in tools)
    assert _handle(t_mod, {"id": 3, "method": "prompts/list"})["result"] == {"prompts": []}
    assert _handle(t_mod, {"id": 4, "method": "resources/list"})["result"] == {"resources": []}
    assert _handle(t_mod, {"method": "notifications/initialized"}) is None
    assert _handle(t_mod, {"id": 9, "result": {}}) is None                 # a response, not a request
    e = _handle(t_mod, {"id": 5, "method": "nope/what"})
    assert e["error"]["code"] == -32601 and e["id"] == 5
    assert _handle(t_mod, "junk")["error"]["code"] == -32600


def test_mcp_handle_tools_call(t_mod):
    r = _handle(t_mod, {"id": 7, "method": "tools/call",
                        "params": {"name": "list_sessions", "arguments": {"repo": "ff"}}})
    assert r["result"]["isError"] is False
    assert json.loads(r["result"]["content"][0]["text"]) == {"ok": "list_sessions", "args": {"repo": "ff"}}
    r = _handle(t_mod, {"id": 8, "method": "tools/call", "params": {"name": "nope"}})
    assert r["result"]["isError"] and "unknown tool: nope" in r["result"]["content"][0]["text"]

    def boom(n, a):
        raise ValueError("give me keywords")
    r = _handle(t_mod, {"id": 9, "method": "tools/call", "params": {"name": "find_sessions"}}, boom)
    assert r["result"]["isError"] and r["result"]["content"][0]["text"] == "ValueError: give me keywords"
    r = _handle(t_mod, {"id": 10, "method": "tools/call", "params": {"name": "find_sessions"}},
                lambda n, a: {"when": object()})
    assert r["result"]["isError"] is False                              # default=str, never a crash


def test_mcp_registered(t_mod, tmp_path):
    p = tmp_path / "claude.json"
    assert t_mod._mcp_registered(str(p)) is None
    p.write_text("{bad")
    assert t_mod._mcp_registered(str(p)) is None
    p.write_text(json.dumps({"mcpServers": {"cashfwd": {}}}))
    assert t_mod._mcp_registered(str(p)) is False
    p.write_text(json.dumps({"mcpServers": {"sessions": {"command": "/x/t", "args": ["mcp"]}}}))
    assert t_mod._mcp_registered(str(p)) is True
    p.write_text(json.dumps([1, 2]))
    assert t_mod._mcp_registered(str(p)) is False


def test_doctor_reports_unregistered_mcp(t_mod):
    assert any("t mcp --install" in l for l in t_mod._doctor_findings({"mcp_sessions": False}))
    assert t_mod._doctor_findings({"mcp_sessions": True}) == ["✓ nothing suspicious found"]
    assert t_mod._doctor_findings({"mcp_sessions": None}) == ["✓ nothing suspicious found"]


def test_mcp_allow_state(t_mod, tmp_path):
    p = tmp_path / "settings.json"
    assert t_mod._mcp_allow_state(str(p)) is None          # no file yet
    p.write_text("{bad")
    assert t_mod._mcp_allow_state(str(p)) is None          # unreadable: not ours to judge
    p.write_text(json.dumps({"model": "opus"}))
    assert t_mod._mcp_allow_state(str(p)) == "none"
    p.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}}))
    assert t_mod._mcp_allow_state(str(p)) == "none"
    p.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)", "mcp__sessions"]}}))
    assert t_mod._mcp_allow_state(str(p)) == "server"
    p.write_text(json.dumps({"permissions": {"allow": ["mcp__sessions__session_detail"]}}))
    assert t_mod._mcp_allow_state(str(p)) == "tools"
    # a neighbouring server must not read as ours
    p.write_text(json.dumps({"permissions": {"allow": ["mcp__sessionsX"]}}))
    assert t_mod._mcp_allow_state(str(p)) == "none"
    p.write_text(json.dumps({"permissions": {"allow": [{"rule": "mcp__sessions"}]}}))
    assert t_mod._mcp_allow_state(str(p)) == "none"        # non-string entries ignored


def test_mcp_allow_write_is_add_only_and_idempotent(t_mod, tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"model": "opus", "permissions": {"allow": ["Bash(ls)"]}}))
    assert t_mod._mcp_allow_write(str(p)) is True
    data = json.loads(p.read_text())
    assert data["permissions"]["allow"] == ["Bash(ls)", "mcp__sessions"]
    assert data["model"] == "opus"                          # everything else survives
    assert t_mod._mcp_allow_write(str(p)) is False          # runs on every dots
    assert not list(tmp_path.glob("*.tmp"))                 # tmp + os.replace, nothing left

    # no permissions block at all: create one
    q = tmp_path / "bare.json"
    q.write_text(json.dumps({"model": "opus"}))
    assert t_mod._mcp_allow_write(str(q)) is True
    assert json.loads(q.read_text())["permissions"]["allow"] == ["mcp__sessions"]


def test_mcp_allow_write_never_widens_a_hand_narrowed_set(t_mod, tmp_path):
    # per-tool rules are a deliberate choice — never silently widened back out
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"permissions": {"allow": ["mcp__sessions__session_detail"]}}))
    assert t_mod._mcp_allow_write(str(p)) is False
    assert json.loads(p.read_text())["permissions"]["allow"] == ["mcp__sessions__session_detail"]
    # and an unreadable/foreign-shaped file is left exactly as found
    for junk in ("{bad", json.dumps([1, 2]), json.dumps({"permissions": {"allow": "all"}})):
        p.write_text(junk)
        assert t_mod._mcp_allow_write(str(p)) is False
        assert p.read_text() == junk


def test_doctor_reports_missing_mcp_allow_rule(t_mod):
    out = t_mod._doctor_findings({"mcp_allow": "none"})
    assert any("mcp__sessions" in l and "every sessions lookup prompts" in l for l in out)
    for state in ("server", "tools", None):
        assert t_mod._doctor_findings({"mcp_allow": state}) == ["✓ nothing suspicious found"]


def test_sessions_mcp_is_allowed_by_default_in_the_shipped_setup(t_mod):
    # the two halves of "allowed by default": the seed a fresh box copies, and the
    # merge every existing box gets — the latter only if it sits ABOVE the
    # DOTFILES_LINKS_ONLY exit, or a plain `dots` would never reach it
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(t_mod.__file__)))
    with open(os.path.join(root, "claude", "settings.json.example")) as f:
        assert t_mod._MCP_ALLOW_RULE in json.load(f)["permissions"]["allow"]
    with open(os.path.join(root, "install.sh")) as f:
        sh = f.read()
    assert sh.index("install_claude_mcp_allow\n") < sh.index('if [[ -n "${DOTFILES_LINKS_ONLY:-}" ]]; then\n    exit 0')


# ─── _vis_len / _term_rows / _page_window (the phone-width wizard fixes) ────────

def test_vis_len_ignores_sgr_escapes(t_mod):
    st = t_mod.Style(tty=True)
    assert t_mod._vis_len(f"{st.c}◆{st.r}  {st.b}hosts{st.r}") == len("◆  hosts")
    assert t_mod._vis_len("plain") == 5


def test_term_rows_counts_wrapped_rows_not_lines(t_mod):
    # a 53-column picker head on a 44-column phone terminal takes TWO rows —
    # the off-by-one that drifted the wizard down the screen on every keypress
    head = "◆  hosts  ↑↓ move · space mark · enter pick · q quit"
    assert len(head) == 52
    assert t_mod._term_rows(head, 44) == 2
    assert t_mod._term_rows(head, 80) == 1
    assert t_mod._term_rows("", 44) == 1          # an empty line still occupies a row
    assert t_mod._term_rows("x" * 88, 44) == 2    # exact multiple: no phantom row
    assert t_mod._term_rows("x" * 89, 44) == 3


def test_page_window_shows_everything_when_it_fits(t_mod):
    assert t_mod._page_window([1, 1, 1], 0, 5) == (0, 3)
    assert t_mod._page_window([], 0, 5) == (0, 0)
    # top is irrelevant when the whole block fits
    assert t_mod._page_window([1, 1, 1], 2, 3) == (0, 3)


def test_page_window_reserves_the_markers(t_mod):
    # 6 single-row lines in 4 rows: at the top there is no "above" marker, so
    # 3 lines + the "below" marker fill the 4 rows
    assert t_mod._page_window([1] * 6, 0, 4) == (0, 3)
    # scrolled one down: "above" + 2 lines + "below"
    assert t_mod._page_window([1] * 6, 1, 4) == (1, 3)


def test_page_window_clamps_top_so_the_block_never_scrolls_past_its_end(t_mod):
    heights = [1] * 6
    # from lo=3: "above" + lines 3,4,5 = 4 rows, no "below" needed → the last top
    assert t_mod._page_window(heights, 3, 4) == (3, 6)
    assert t_mod._page_window(heights, 99, 4) == (3, 6)
    assert t_mod._page_window(heights, -5, 4) == (0, 3)


def test_page_window_accounts_for_wrapped_lines(t_mod):
    # a review whose argv lines wrap to 2–3 rows each on a phone
    heights = [1, 3, 3, 2, 3, 1]
    lo, hi = t_mod._page_window(heights, 0, 8)
    assert (lo, hi) == (0, 3)            # 1 + 3 + 3 = 7 rows + the "below" marker
    lo, hi = t_mod._page_window(heights, 2, 8)
    assert (lo, hi) == (2, 4)            # "above" + 3 + 2 + "below" = 7 ≤ 8; +3 would not fit


def test_page_window_always_shows_at_least_one_line(t_mod):
    # a single line taller than the whole window is still shown rather than nothing
    assert t_mod._page_window([9, 1], 0, 4) == (0, 1)
    assert t_mod._page_window([1, 9], 1, 4) == (1, 2)


# ─── _ls_scope (t ls [repo] [-a]) ────────────────────────────────────────────────

def test_ls_scope_explicit_repo_from_anywhere(t_mod, tmp_path, monkeypatch):
    # `t ls dot -r` from ~ must scope to dot's dir — the alias, not the cwd, decides.
    cfg = _config_with(t_mod, tmp_path, monkeypatch,
                       {"dot": "/Users/me/code/dotfiles", "api": "/Users/me/code/api"},
                       worktree_root="/Users/me/code/.worktrees")
    scope, wt = t_mod._ls_scope(cfg, "dot", False, "/Users/me")
    assert scope == "/Users/me/code/dotfiles"
    assert wt == "/Users/me/code/.worktrees/dotfiles"


def test_ls_scope_defaults_to_cwd_repo(t_mod, tmp_path, monkeypatch):
    cfg = _config_with(t_mod, tmp_path, monkeypatch, {"api": "/Users/me/code/api"},
                       worktree_root="/Users/me/wt")
    assert t_mod._ls_scope(cfg, None, False, "/Users/me/code/api/src") == \
        ("/Users/me/code/api", "/Users/me/wt/api")
    assert t_mod._ls_scope(cfg, None, False, "/Users/me") == ("", "")


def test_ls_scope_all_widens(t_mod, tmp_path, monkeypatch):
    cfg = _config_with(t_mod, tmp_path, monkeypatch, {"api": "/Users/me/code/api"})
    assert t_mod._ls_scope(cfg, None, True, "/Users/me/code/api") == ("", "")


def test_ls_scope_unknown_repo_names_choices(t_mod, tmp_path, monkeypatch):
    cfg = _config_with(t_mod, tmp_path, monkeypatch, {"api": "/x", "dot": "/y"})
    with pytest.raises(ValueError) as e:
        t_mod._ls_scope(cfg, "nope", False, "/")
    assert "api, dot" in str(e.value)


def test_ls_scope_repo_with_all_is_refused(t_mod, tmp_path, monkeypatch):
    cfg = _config_with(t_mod, tmp_path, monkeypatch, {"api": "/x"})
    with pytest.raises(ValueError):
        t_mod._ls_scope(cfg, "api", True, "/")


def test_ls_parser_accepts_optional_repo(t_mod):
    p = t_mod.build_parser()
    a = p.parse_args(["ls", "dot", "-r"])
    assert (a.repo, a.remote, a.all) == ("dot", True, False)
    a = p.parse_args(["ls", "-a"])
    assert (a.repo, a.all) == (None, True)
