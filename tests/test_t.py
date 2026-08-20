"""Unit tests for the pure logic in bin/t.

Scope is deliberately the deterministic helpers — config parsing, path→repo
resolution, the capture-pane cleaner, row parsing/filtering. Subprocess/ssh/tmux
verbs (zsh_capture, _delegate, the cmd_* handlers) are out of scope by design.
"""

import os


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
