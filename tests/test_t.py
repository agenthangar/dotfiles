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


# ─── t todo — key resolution ───────────────────────────────────────────────────

def _todo_cfg(t_mod, tmp_path, monkeypatch):
    return _config_with(t_mod, tmp_path, monkeypatch,
                        {"dotfiles": "/code/dotfiles", "ff": "/code/financial-forecast"},
                        worktree_root="/wt")


def test_todo_key_prefers_the_worktree_path(t_mod, tmp_path, monkeypatch):
    cfg = _todo_cfg(t_mod, tmp_path, monkeypatch)
    # The worktree path carries the slot, and it wins over a disagreeing tmux name —
    # the path is where the code actually is.
    assert t_mod._todo_key(cfg, "/wt/dotfiles/3") == "dotfiles-3"
    assert t_mod._todo_key(cfg, "/wt/dotfiles/3", tmux_name="dev-ff-9") == "dotfiles-3"


def test_todo_key_worktree_is_keyed_on_the_basename_not_the_alias(t_mod, tmp_path, monkeypatch):
    cfg = _todo_cfg(t_mod, tmp_path, monkeypatch)
    # Worktree dirs are $DEV_WORKTREE_ROOT/<repo basename>/<slot> — deliberately the
    # basename, so the path is identical on every host — but the KEY uses the local
    # alias, so `ff` (at ~/code/financial-forecast) lands as ff-2, not financial-forecast-2.
    assert t_mod._todo_key(cfg, "/wt/financial-forecast/2") == "ff-2"


def test_todo_key_falls_back_to_the_tmux_session_name(t_mod, tmp_path, monkeypatch):
    cfg = _todo_cfg(t_mod, tmp_path, monkeypatch)
    # Shared-tree (worktree opt-out) repo: the path has no slot, the session name does.
    assert t_mod._todo_key(cfg, "/code/dotfiles", tmux_name="dev-dotfiles-2") == "dotfiles-2"
    # …and it works from anywhere inside that slot's tmux session, repo dir or not.
    assert t_mod._todo_key(cfg, "/tmp", tmux_name="dev-dotfiles-2") == "dotfiles-2"


def test_todo_key_splits_the_tmux_name_on_the_last_dash(t_mod, tmp_path, monkeypatch):
    cfg = _todo_cfg(t_mod, tmp_path, monkeypatch)
    # Multi-dash alias must survive (the _dev_* last-dash rule).
    assert t_mod._todo_key(cfg, "/tmp", tmux_name="dev-my-long-repo-7") == "my-long-repo-7"
    # Not a dev slot, or no numeric slot → not a key.
    assert t_mod._todo_key(cfg, "/tmp", tmux_name="scratchpad") == "scratch"
    assert t_mod._todo_key(cfg, "/tmp", tmux_name="dev-dotfiles-main") == "scratch"
    assert t_mod._todo_key(cfg, "/tmp", tmux_name="dev-4") == "scratch"


def test_todo_key_repo_dir_without_a_slot(t_mod, tmp_path, monkeypatch):
    cfg = _todo_cfg(t_mod, tmp_path, monkeypatch)
    assert t_mod._todo_key(cfg, "/code/dotfiles") == "dotfiles"
    assert t_mod._todo_key(cfg, "/code/dotfiles/bin") == "dotfiles"


def test_todo_key_scratch_and_override(t_mod, tmp_path, monkeypatch):
    cfg = _todo_cfg(t_mod, tmp_path, monkeypatch)
    # Nowhere in particular still has somewhere to write, so a quick add never errors.
    assert t_mod._todo_key(cfg, "/tmp") == "scratch"
    # -s wins over everything.
    assert t_mod._todo_key(cfg, "/wt/dotfiles/3", tmux_name="dev-ff-1",
                           override="ff-9") == "ff-9"


# ─── t todo — paths and persistence ────────────────────────────────────────────

def test_todo_dir_honours_xdg_state_home(t_mod, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", "/xdg/state")
    assert t_mod._todo_dir() == "/xdg/state/t/todo"
    assert t_mod._todo_path("dotfiles-1") == "/xdg/state/t/todo/dotfiles-1.json"


def test_todo_dir_defaults_under_local_state(t_mod, monkeypatch):
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(t_mod, "HOME", "/home/me")
    assert t_mod._todo_dir() == "/home/me/.local/state/t/todo"


def test_todo_load_missing_file_is_an_empty_list(t_mod, tmp_path):
    data = t_mod._todo_load(str(tmp_path / "nope.json"))
    assert data == {"v": 1, "next_id": 1, "items": []}


def test_todo_load_corrupt_file_is_an_empty_list(t_mod, tmp_path):
    # A half-written file must not make the command unusable.
    p = tmp_path / "x.json"
    p.write_text('{"items": [{"id": 1, "te')
    assert t_mod._todo_load(str(p))["items"] == []
    p.write_text('["not", "a", "dict"]')
    assert t_mod._todo_load(str(p))["items"] == []


def test_todo_load_repairs_next_id(t_mod, tmp_path):
    # A stale next_id (hand-edited, or a sync that kept the older scalar) must never
    # hand out an id that is already taken.
    p = tmp_path / "x.json"
    p.write_text('{"v": 1, "next_id": 2, "items": ['
                 '{"id": 1, "text": "a"}, {"id": 7, "text": "b"}]}')
    assert t_mod._todo_load(str(p))["next_id"] == 8


def test_todo_save_then_load_roundtrips(t_mod, tmp_path):
    path = str(tmp_path / "deep" / "dotfiles-1.json")
    data = {"v": 1, "next_id": 2,
            "items": [{"id": 1, "text": "café ☕", "done": False,
                       "added": 1, "done_at": None, "deleted": False}]}
    t_mod._todo_save(path, data)          # creates the parent dir
    assert t_mod._todo_load(path) == data
    assert not os.path.exists(path + ".tmp")


# ─── t todo — the mutation core ────────────────────────────────────────────────

def _fresh(t_mod):
    return dict(t_mod._TODO_EMPTY, items=[])


def _add(t_mod, data, *texts):
    for text in texts:
        t_mod._todo_apply(data, "add", text.split(), now=100)
    return data


def test_todo_add_assigns_rising_ids(t_mod):
    data = _fresh(t_mod)
    msg, rc = t_mod._todo_apply(data, "add", ["rebase", "onto", "main"], now=100)
    assert rc == 0 and "#1" in msg
    t_mod._todo_apply(data, "add", ["second"], now=100)
    assert [i["id"] for i in data["items"]] == [1, 2]
    assert data["items"][0]["text"] == "rebase onto main"
    assert data["next_id"] == 3


def test_todo_add_needs_text(t_mod):
    data = _fresh(t_mod)
    msg, rc = t_mod._todo_apply(data, "add", ["   "], now=100)
    assert rc == 2 and data["items"] == []


def test_todo_done_hides_the_item(t_mod):
    data = _add(t_mod, _fresh(t_mod), "one", "two")
    msg, rc = t_mod._todo_apply(data, "done", ["1"], now=200)
    assert rc == 0 and "#1" in msg
    assert data["items"][0]["done"] and data["items"][0]["done_at"] == 200
    assert [i["id"] for i in t_mod._todo_open(data)] == [2]


def test_todo_reopen_is_internal_only(t_mod):
    # `undone` was retired as a typed verb, but the bare-id picker still needs to
    # un-tick — it goes through the underscore-prefixed action nobody types.
    data = _add(t_mod, _fresh(t_mod), "one")
    t_mod._todo_apply(data, "done", ["1"], now=200)
    msg, rc = t_mod._todo_apply(data, "_reopen", ["1"], now=300)
    assert rc == 0 and not data["items"][0]["done"]
    assert data["items"][0]["done_at"] is None
    assert t_mod._todo_apply(data, "_reopen", ["9"], now=300)[1] == 1
    assert t_mod._todo_apply(data, "_reopen", [], now=300)[1] == 1


def test_todo_accepts_several_ids_at_once(t_mod):
    data = _add(t_mod, _fresh(t_mod), "one", "two", "three")
    msg, rc = t_mod._todo_apply(data, "done", ["1", "3"], now=200)
    assert rc == 0
    assert [i["id"] for i in t_mod._todo_open(data)] == [2]


def test_todo_unknown_id_reports_without_raising(t_mod):
    data = _add(t_mod, _fresh(t_mod), "one")
    msg, rc = t_mod._todo_apply(data, "done", ["9", "not-a-number"], now=200)
    assert rc == 1
    assert "9" in msg and "not-a-number" in msg
    assert not data["items"][0]["done"]
    # A partial hit still applies what it could, and still reports the miss.
    msg, rc = t_mod._todo_apply(data, "done", ["1", "9"], now=200)
    assert rc == 1 and data["items"][0]["done"]


def test_todo_id_actions_need_an_id(t_mod):
    data = _add(t_mod, _fresh(t_mod), "one")
    for action in ("done", "undone", "rm"):
        assert t_mod._todo_apply(data, action, [], now=200)[1] == 2


def test_todo_rm_is_a_tombstone_not_a_delete(t_mod):
    # csync's rsync has no --delete, so a physically dropped item comes back from
    # iCloud. The item must survive in the file, flagged.
    data = _add(t_mod, _fresh(t_mod), "one", "two")
    t_mod._todo_apply(data, "rm", ["1"], now=200)
    assert len(data["items"]) == 2
    assert data["items"][0]["deleted"] and data["items"][0]["deleted_at"] == 200
    assert [i["id"] for i in t_mod._todo_open(data)] == [2]
    # …and a tombstoned item is gone for good: not listable, not addressable.
    assert t_mod._todo_find(data, "1") is None
    assert t_mod._todo_apply(data, "done", ["1"], now=300)[1] == 1
    assert [i["id"] for i in t_mod._todo_visible(data, show_all=True)] == [2]


def test_todo_unknown_action_is_one_line(t_mod):
    data = _fresh(t_mod)
    msg, rc = t_mod._todo_apply(data, "fix", ["the", "thing"], now=300)
    assert rc == 2 and "\n" not in msg          # the add-hint second line is gone
    assert "add, done, rm, mv, ls" in msg
    assert data["items"] == []


def test_todo_retired_verbs_explain_themselves(t_mod):
    # Muscle memory from the week these existed should get a pointer, not a dead end.
    for verb, needle in (("clear", "purge themselves"), ("edit", "add it again"),
                         ("undone", "-a"), ("purge", "purge themselves"),
                         ("reopen", "-a")):
        msg, rc = t_mod._todo_apply(_fresh(t_mod), verb, [], now=300)
        assert rc == 2 and "is gone" in msg and needle in msg


# ─── t todo — rendering ────────────────────────────────────────────────────────

def _body(lines):
    """Drop the trailing blank + action footer every todo view now appends, so these
    assertions stay about the items themselves."""
    return lines[:-2] if len(lines) >= 2 and lines[-2] == "" else lines


def _plain(t_mod):
    """A colourless Style so assertions can match text, not escape codes."""
    st = t_mod.Style()
    for attr in ("g", "c", "y", "b", "r"):
        setattr(st, attr, "")
    return st


def test_todo_render_lists_open_items(t_mod):
    data = _add(t_mod, _fresh(t_mod), "one", "two")
    t_mod._todo_apply(data, "done", ["1"], now=200)
    lines = _body(t_mod._todo_render(data, "dotfiles-1", st=_plain(t_mod)))
    assert lines[0] == "dotfiles-1 — 1 open"
    assert [l.strip() for l in lines[1:]] == ["2  ◻ two"]


def test_todo_render_all_flag_shows_done_and_a_tally(t_mod):
    data = _add(t_mod, _fresh(t_mod), "one", "two")
    t_mod._todo_apply(data, "done", ["1"], now=200)
    lines = _body(t_mod._todo_render(data, "dotfiles-1", show_all=True, st=_plain(t_mod)))
    assert lines[0] == "dotfiles-1 — 1 open · 1 done"
    assert [l.strip() for l in lines[1:]] == ["1  ✓ one", "2  ◻ two"]


def test_todo_render_always_ends_with_the_action_footer(t_mod):
    # Bare `t todo` is the discovery surface: the verbs must be visible without -h.
    for data in (_fresh(t_mod), _add(t_mod, _fresh(t_mod), "one")):
        lines = t_mod._todo_render(data, "dotfiles-1", st=_plain(t_mod))
        assert lines[-2] == ""
        for verb in ("t todo add", "t todo done", "t todo <id>", "-A"):
            assert verb in lines[-1]
    empty = t_mod._todo_render(_fresh(t_mod), "dotfiles-1", st=_plain(t_mod))
    assert empty[0] == "dotfiles-1 — nothing open"


def test_todo_render_truncates_to_width(t_mod):
    data = _add(t_mod, _fresh(t_mod), "x" * 200)
    lines = _body(t_mod._todo_render(data, "k", width=40, st=_plain(t_mod)))
    assert all(len(l) <= 40 for l in lines)
    assert lines[1].endswith("…")


def test_todo_render_all_groups_by_slot_and_skips_empty(t_mod):
    a = _add(t_mod, _fresh(t_mod), "first", "second")
    b = _add(t_mod, _fresh(t_mod), "other")
    empty = _fresh(t_mod)
    done_only = _add(t_mod, _fresh(t_mod), "finished")
    t_mod._todo_apply(done_only, "done", ["1"], now=200)
    lines = t_mod._todo_render_all(
        [("dotfiles-1", a), ("ff-2", b), ("gone-3", empty), ("tidy-4", done_only)],
        st=_plain(t_mod))
    lines = _body(lines)
    assert "dotfiles-1" in lines[0] and "first" in lines[0]
    assert lines[1].strip().startswith("2")       # continuation row: no repeated key
    assert "dotfiles-1" not in lines[1]
    assert "ff-2" in lines[2] and "other" in lines[2]
    # A slot with nothing open never takes a row.
    assert not any("gone-3" in l or "tidy-4" in l for l in lines)


def test_todo_render_all_says_so_when_everything_is_clear(t_mod):
    lines = t_mod._todo_render_all([("a-1", _fresh(t_mod))], st=_plain(t_mod))
    assert _body(lines) == ["nothing open in any slot"]


# ─── t todo — a slot sees its repo-level list ──────────────────────────────────

def test_todo_parent_resolves_a_slot_to_its_repo(t_mod, tmp_path, monkeypatch):
    repos = _todo_cfg(t_mod, tmp_path, monkeypatch).repos
    assert t_mod._todo_parent("dotfiles-3", repos) == "dotfiles"
    assert t_mod._todo_parent("dotfiles", repos) is None      # already repo-level
    assert t_mod._todo_parent("scratch", repos) is None
    # The head must be a REAL alias and the tail numeric, or `financial-forecast`
    # would read as slot "forecast" of a repo "financial".
    assert t_mod._todo_parent("financial-forecast", repos) is None
    assert t_mod._todo_parent("dotfiles-main", repos) is None


def test_todo_shared_is_none_when_the_repo_list_is_empty(t_mod, tmp_path, monkeypatch):
    # The common case: a slot with no shared notes pays one stat and renders as before.
    cfg = _todo_cfg(t_mod, tmp_path, monkeypatch)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert t_mod._todo_shared(cfg, "dotfiles-3") is None
    assert t_mod._todo_shared(cfg, "dotfiles") is None        # already repo-level
    t_mod._todo_save(t_mod._todo_path("dotfiles"), _add(t_mod, _fresh(t_mod), "hello"))
    assert t_mod._todo_shared(cfg, "dotfiles-3")[0] == "dotfiles"


def test_todo_render_marks_shared_items_with_a_diamond(t_mod, tmp_path, monkeypatch):
    # ◇ not ◻, because ids are per-list and both sections can hold a #3 — which list
    # an item lives in has to be readable at a glance.
    own = _add(t_mod, _fresh(t_mod), "fix the thing")
    shared = _add(t_mod, _fresh(t_mod), "hello")
    out = "\n".join(t_mod._todo_render(own, "dotfiles-3", shared=("dotfiles", shared)))
    assert "◻ fix the thing" in out and "◇ hello" in out
    assert out.index("◻ fix the thing") < out.index("◇ hello")   # own work first
    assert "dotfiles — 1 open" in out


def test_todo_render_hides_a_shared_section_with_nothing_to_show(t_mod):
    # No repo list, or one holding only ticked-off items, renders exactly as before.
    own = _add(t_mod, _fresh(t_mod), "fix the thing")
    plain = t_mod._todo_render(own, "dotfiles-3")
    assert t_mod._todo_render(own, "dotfiles-3", shared=None) == plain
    done = _add(t_mod, _fresh(t_mod), "hello")
    t_mod._todo_apply(done, "done", ["1"], now=100)
    assert t_mod._todo_render(own, "dotfiles-3", shared=("dotfiles", done)) == plain
    # -a widens to it, since the item is still live and can be removed or re-filed.
    assert "✓ hello" in "\n".join(
        t_mod._todo_render(own, "dotfiles-3", show_all=True, shared=("dotfiles", done)))


def test_todo_statusline_carries_the_repo_level_list(t_mod, tmp_path, monkeypatch):
    # The bar was keyed strictly on the slot, so a note jotted at repo level — the
    # cross-slot work — was invisible from every place you actually work.
    cfg = _todo_cfg(t_mod, tmp_path, monkeypatch)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("NO_COLOR", "1")
    t_mod._todo_save(t_mod._todo_path("dotfiles-3"), _add(t_mod, _fresh(t_mod), "own"))
    t_mod._todo_save(t_mod._todo_path("dotfiles"), _add(t_mod, _fresh(t_mod), "hello"))
    pay = {"workspace": {"current_dir": "/wt/dotfiles/3"}}
    assert t_mod._todo_statusline(pay, cfg) == "dotfiles-3 │ ◻ own  ◇ hello"
    # A repo-level cwd is already the shared list; it must not double up.
    assert t_mod._todo_statusline({"workspace": {"current_dir": "/code/dotfiles"}},
                                  cfg) == "dotfiles │ ◻ hello"


def test_todo_statusline_shared_only_slot_is_not_empty(t_mod, tmp_path, monkeypatch):
    cfg = _todo_cfg(t_mod, tmp_path, monkeypatch)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("NO_COLOR", "1")
    t_mod._todo_save(t_mod._todo_path("dotfiles"), _add(t_mod, _fresh(t_mod), "hello"))
    assert t_mod._todo_statusline({"workspace": {"current_dir": "/wt/dotfiles/3"}},
                                  cfg) == "dotfiles-3 │ ◇ hello"


# ─── t todo — statusline ───────────────────────────────────────────────────────

def _statusline(t_mod, tmp_path, monkeypatch, payload, key=None, texts=(),
                items_n=1, width=80):
    cfg = _todo_cfg(t_mod, tmp_path, monkeypatch)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    if key:
        data = _add(t_mod, _fresh(t_mod), *texts)
        t_mod._todo_save(t_mod._todo_path(key), data)
    monkeypatch.setenv("NO_COLOR", "1")   # assert on the layout, not the escapes
    return t_mod._todo_statusline(payload, cfg, items_n, width)


def test_todo_statusline_renders_slot_model_and_top_item(t_mod, tmp_path, monkeypatch):
    line = _statusline(t_mod, tmp_path, monkeypatch,
                       {"workspace": {"current_dir": "/wt/dotfiles/1"},
                        "model": {"display_name": "Opus"}},
                       key="dotfiles-1", texts=("rebase onto main", "second"))
    assert line == "dotfiles-1 · Opus │ ◻ rebase onto main  ◻ second"


def test_todo_statusline_prefers_workspace_over_cwd(t_mod, tmp_path, monkeypatch):
    # They differ when Claude is started from a subdir; workspace.current_dir is
    # the project root and is what the slot is keyed on.
    line = _statusline(t_mod, tmp_path, monkeypatch,
                       {"cwd": "/tmp", "workspace": {"current_dir": "/wt/financial-forecast/2"}})
    assert line.startswith("ff-2")


def test_todo_statusline_survives_a_bare_payload(t_mod, tmp_path, monkeypatch):
    # A statusline that raises leaves the TUI with a permanently broken status bar,
    # so every field has to be optional.
    for payload in ({}, {"model": None, "workspace": "nonsense"}, []):
        line = _statusline(t_mod, tmp_path, monkeypatch, payload)
        assert line.endswith("│ nothing open")


def test_todo_statusline_truncates_a_long_item(t_mod, tmp_path, monkeypatch):
    # A single item longer than the bar fills it exactly and ellipses — it must never
    # wrap, or the one-line contract breaks on the narrowest host.
    for width in (40, 80, 120):
        line = _statusline(t_mod, tmp_path, monkeypatch,
                           {"workspace": {"current_dir": "/wt/dotfiles/1"}},
                           key="dotfiles-1", texts=("y" * 400,), width=width)
        assert line.endswith("…") and len(line) <= width and "\n" not in line


def test_todo_statusline_colours_unconditionally(t_mod, tmp_path, monkeypatch):
    # Style() gates on isatty and this stdout is always the pipe Claude Code reads,
    # so the shared helper would render every bar plain. NO_COLOR still opts out.
    monkeypatch.delenv("NO_COLOR", raising=False)
    cfg = _todo_cfg(t_mod, tmp_path, monkeypatch)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    t_mod._todo_save(t_mod._todo_path("dotfiles-1"), _add(t_mod, _fresh(t_mod), "one"))
    line = t_mod._todo_statusline({"workspace": {"current_dir": "/wt/dotfiles/1"}}, cfg)
    assert line.startswith("\033[2m") and "\033[36m◻\033[0m one" in line


def test_todo_statusline_falls_back_when_no_item_fits(t_mod, tmp_path, monkeypatch):
    # Too narrow for even a truncated item: report the count rather than leave a
    # dangling │ with nothing after it.
    line = _statusline(t_mod, tmp_path, monkeypatch,
                       {"workspace": {"current_dir": "/wt/dotfiles/1"}},
                       key="dotfiles-1", texts=("one", "two"), width=26)
    assert line == "dotfiles-1 │ 2 open"


# ─── t todo — action aliases and cross-list move ───────────────────────────────

def test_todo_action_aliases_resolve(t_mod):
    # `t todo list` was a dead end; none of these collide with a canonical name.
    assert t_mod._todo_canon("list") == "ls"
    assert t_mod._todo_canon("delete") == "rm"
    assert t_mod._todo_canon("x") == "done"
    assert len(t_mod._TODO_ALIASES) == 4        # four, not thirteen
    # "check" is deliberately NOT an alias — it reads as "show me" as much as "check
    # off", so it stays an unknown action rather than surprising either way.
    assert t_mod._todo_canon("check") == "check"
    assert t_mod._todo_canon("move") == "mv"
    # canonical names pass through, unknown words stay unknown (so the hint still fires)
    assert t_mod._todo_canon("add") == "add"
    assert t_mod._todo_canon("frobnicate") == "frobnicate"


def test_todo_apply_accepts_an_alias(t_mod):
    data = _add(t_mod, _fresh(t_mod), "one")
    msg, rc = t_mod._todo_apply(data, "delete", ["1"], now=200)
    assert rc == 0 and data["items"][0]["deleted"]


def test_todo_move_across_lists(t_mod):
    src = _add(t_mod, _fresh(t_mod), "one", "two")
    dst = _add(t_mod, _fresh(t_mod), "already here")
    msg, rc = t_mod._todo_move(src, dst, "1", "ff-3", now=200)
    assert rc == 0 and "ff-3" in msg
    # gone from the source, present in the destination, text intact
    assert [i["id"] for i in t_mod._todo_open(src)] == [2]
    assert [i["text"] for i in t_mod._todo_open(dst)] == ["already here", "one"]


def test_todo_move_reids_in_the_destination(t_mod):
    # ids are per-list, so the moved item takes the destination's next id, not its own
    src = _add(t_mod, _fresh(t_mod), "a", "b", "c")
    dst = _fresh(t_mod)
    t_mod._todo_move(src, dst, "3", "ff-3", now=200)
    assert [i["id"] for i in dst["items"]] == [1]
    assert dst["next_id"] == 2


def test_todo_move_tombstones_the_source_item(t_mod):
    # same reason as rm: csync has no --delete, so the source file must carry the move
    src = _add(t_mod, _fresh(t_mod), "one")
    dst = _fresh(t_mod)
    t_mod._todo_move(src, dst, "1", "ff-3", now=200)
    assert len(src["items"]) == 1
    assert src["items"][0]["deleted"] and src["items"][0]["deleted_at"] == 200
    # …and the moved copy is NOT itself a tombstone
    assert not dst["items"][0]["deleted"] and dst["items"][0]["deleted_at"] is None


def test_todo_move_unknown_id(t_mod):
    src = _add(t_mod, _fresh(t_mod), "one")
    dst = _fresh(t_mod)
    msg, rc = t_mod._todo_move(src, dst, "9", "ff-3", now=200)
    assert rc == 1 and dst["items"] == [] and not src["items"][0]["deleted"]


def test_todo_statusline_packs_items_into_the_width(t_mod, tmp_path, monkeypatch):
    texts = ("rebase onto main", "fix the ledger rollover", "ask about the beam gap")
    wide = _statusline(t_mod, tmp_path, monkeypatch,
                       {"workspace": {"current_dir": "/wt/dotfiles/1"}},
                       key="dotfiles-1", texts=texts, width=90)
    narrow = _statusline(t_mod, tmp_path, monkeypatch,
                         {"workspace": {"current_dir": "/wt/dotfiles/1"}},
                         key="dotfiles-1", texts=texts, width=45)
    assert "\n" not in wide and "\n" not in narrow      # one line, always
    assert len(narrow) <= 45 and narrow.count("◻") < wide.count("◻")
    assert wide.endswith("more") and "+" in wide         # the unshown remainder is counted


def test_todo_statusline_multiline_lists_items(t_mod, tmp_path, monkeypatch):
    out = _statusline(t_mod, tmp_path, monkeypatch,
                      {"workspace": {"current_dir": "/wt/dotfiles/1"}},
                      key="dotfiles-1", texts=("one", "two", "three"), items_n=2)
    lines = out.split("\n")
    assert lines[0].endswith("│ 3 open")
    assert lines[1].strip() == "◻ 1 one" and lines[2].strip() == "◻ 2 two"
    assert lines[3].strip() == "+1 more"


def test_todo_statusline_multiline_without_a_remainder(t_mod, tmp_path, monkeypatch):
    out = _statusline(t_mod, tmp_path, monkeypatch,
                      {"workspace": {"current_dir": "/wt/dotfiles/1"}},
                      key="dotfiles-1", texts=("one",), items_n=5)
    assert out.split("\n") == ["dotfiles-1 │ 1 open", "  ◻ 1 one"]


def test_todo_statusline_empty_list_is_one_line_either_way(t_mod, tmp_path, monkeypatch):
    for n in (1, 5):
        out = _statusline(t_mod, tmp_path, monkeypatch, {}, items_n=n)
        assert out == "scratch │ nothing open"


# ─── t todo — the slotless-add destination picker ──────────────────────────────

def _todo_row(slot, summary=""):
    return {"host": "local", "sid": "-", "cwd": "", "slot": slot,
            "state": "", "context": "", "summary": summary}


def test_todo_add_targets_repo_level_is_first(t_mod):
    # fzf preselects line 1, so the current behaviour stays the default — the picker
    # adds a choice, it must not take one away.
    out = t_mod._todo_add_targets("ff", [_todo_row("ff-3", "budget work")], {}, set())
    assert out[0][0] == "ff"
    assert "no slot" in out[0][1]


def test_todo_add_targets_labels_live_slots_with_their_work(t_mod):
    out = t_mod._todo_add_targets(
        "ff", [_todo_row("ff-3", "budget amortization"), _todo_row("ff-15", "pwa banner")], {}, set())
    assert [k for k, _ in out] == ["ff", "ff-3", "ff-15"]
    assert "budget amortization" in out[1][1] and "pwa banner" in out[2][1]


def test_todo_add_targets_unions_three_sources(t_mod):
    # live session, worktree on disk, existing list — each alone is enough to offer
    out = t_mod._todo_add_targets("ff", [_todo_row("ff-1", "live one")],
                                  {"ff-2": 3, "other-9": 1}, {"4"})
    assert [k for k, _ in out] == ["ff", "ff-1", "ff-2", "ff-4"]
    assert "live one" in out[1][1]
    assert "3 open" in out[2][1]
    assert "worktree" in out[3][1]


def test_todo_add_targets_sorts_numerically(t_mod):
    out = t_mod._todo_add_targets("ff", [], {}, {"2", "10", "1"})
    assert [k for k, _ in out] == ["ff", "ff-1", "ff-2", "ff-10"]


def test_todo_add_targets_ignores_other_repos_and_junk(t_mod):
    out = t_mod._todo_add_targets(
        "ff", [_todo_row("dotfiles-1", "x"), _todo_row("ff-abc", "y")],
        {"scratch": 2, "ff": 1}, {"notaslot"})
    assert [k for k, _ in out] == ["ff"]        # nothing else qualified


def test_todo_add_targets_repo_only_when_no_slots_exist(t_mod):
    # a single entry is the caller's signal to skip the picker entirely
    assert len(t_mod._todo_add_targets("ff", [], {}, set())) == 1


def test_todo_slot_arg_takes_a_leading_number_when_text_follows(t_mod):
    # `t todo add 11 fix the ledger` files across slots without leaving the dir.
    assert t_mod._todo_slot_arg(["11", "fix", "the", "ledger"]) == ("11", ["fix", "the", "ledger"])


def test_todo_slot_arg_leaves_a_lone_number_as_text(t_mod):
    # `t todo add 11` — a lone number is an item, not a destination for nothing. This is
    # the case that filed "11" to scratch and read as if it had gone to slot 11.
    assert t_mod._todo_slot_arg(["11"]) == (None, ["11"])
    assert t_mod._todo_slot_arg([]) == (None, [])
    assert t_mod._todo_slot_arg(None) == (None, [])


def test_todo_slot_arg_quoting_is_the_escape_hatch(t_mod):
    # `t todo add "3 more tests"` arrives as ONE token, so nothing is stripped — the way
    # to keep text that really does start with a digit.
    assert t_mod._todo_slot_arg(["3 more tests"]) == (None, ["3 more tests"])
    # …and an unquoted one is read as a slot, which is the accepted collision.
    assert t_mod._todo_slot_arg(["3", "more", "tests"]) == ("3", ["more", "tests"])


def test_todo_slot_arg_ignores_a_non_numeric_head(t_mod):
    assert t_mod._todo_slot_arg(["fix", "11"]) == (None, ["fix", "11"])
    assert t_mod._todo_slot_arg(["v2", "bump"]) == (None, ["v2", "bump"])


def test_todo_canon_aliases_one_per_repo_dir(t_mod, tmp_path, monkeypatch):
    # dot + dotfiles name one tree; offering both would list the same slots twice.
    cfg = _config_with(t_mod, tmp_path, monkeypatch,
                       {"dot": "/code/dotfiles", "dotfiles": "/code/dotfiles",
                        "ff": "/code/financial-forecast"})
    assert t_mod._todo_canon_aliases(cfg) == ["dotfiles", "ff"]


def test_todo_canon_aliases_falls_back_to_the_shortest_key(t_mod, tmp_path, monkeypatch):
    # No key equals the basename → shortest wins, exactly as repo_of_dir chooses.
    cfg = _config_with(t_mod, tmp_path, monkeypatch,
                       {"ff": "/code/financial-forecast", "fcast": "/code/financial-forecast"})
    assert t_mod._todo_canon_aliases(cfg) == ["ff"]


def test_todo_add_targets_all_puts_scratch_first(t_mod):
    # Enter keeps the old from-nowhere behaviour: the picker adds a choice, never takes one.
    out = t_mod._todo_add_targets_all(["ff"], [_todo_row("ff-3", "budget work")], {}, {})
    assert out[0][0] == "scratch"
    assert [k for k, _ in out] == ["scratch", "ff", "ff-3"]


def test_todo_add_targets_all_skips_repos_with_nowhere_to_file(t_mod):
    # ff has a live slot, dotfiles has an existing list, quiet has neither.
    out = t_mod._todo_add_targets_all(
        ["dotfiles", "ff", "quiet"], [_todo_row("ff-3", "x")], {"dotfiles": 2}, {})
    assert [k for k, _ in out] == ["scratch", "dotfiles", "ff", "ff-3"]


def test_todo_add_targets_all_reads_worktrees_per_alias(t_mod):
    out = t_mod._todo_add_targets_all(["ff"], [], {}, {"ff": {"4"}})
    assert [k for k, _ in out] == ["scratch", "ff", "ff-4"]
    assert "worktree" in out[2][1]


def test_todo_add_targets_all_alone_is_just_scratch(t_mod):
    # A single entry is the caller's signal to skip the picker entirely.
    assert len(t_mod._todo_add_targets_all([], [], {}, {})) == 1


def test_todo_scoped_picks_out_one_repos_lists(t_mod):
    entries = [("dotfiles", 1), ("dotfiles-1", 2), ("dotfiles-12", 3),
               ("ff", 4), ("ff-3", 5), ("scratch", 6), ("dotfiles-main", 7)]
    entries = [(k, v) for k, v in entries]
    assert [k for k, _ in t_mod._todo_scoped(entries, "dotfiles")] == [
        "dotfiles", "dotfiles-1", "dotfiles-12"]
    # a non-numeric tail is a different repo's list, not a slot of this one
    assert [k for k, _ in t_mod._todo_scoped(entries, "ff")] == ["ff", "ff-3"]
    assert t_mod._todo_scoped(entries, "nothing") == []


# ─── t todo — no-id action pickers ─────────────────────────────────────────────

def _mixed(t_mod):
    data = _add(t_mod, _fresh(t_mod), "one", "two", "three", "four")
    t_mod._todo_apply(data, "done", ["2"], now=200)
    t_mod._todo_apply(data, "rm", ["4"], now=200)
    return data


def test_todo_pick_candidates_done_offers_only_open(t_mod):
    # marking a done item done again is a no-op, so it must not be offered
    assert [i for i, _ in t_mod._todo_pick_candidates(_mixed(t_mod), "done")] == [1, 3]


def test_todo_pick_candidates_undone_offers_only_finished(t_mod):
    assert [i for i, _ in t_mod._todo_pick_candidates(_mixed(t_mod), "undone")] == [2]


def test_todo_pick_candidates_rm_and_mv_offer_everything_live(t_mod):
    # you may well want to remove or re-file something already ticked off
    for action in ("rm", "mv", "edit"):
        assert [i for i, _ in t_mod._todo_pick_candidates(_mixed(t_mod), action)] == [1, 2, 3]


def test_todo_pick_candidates_never_offers_a_tombstone(t_mod):
    for action in ("done", "undone", "rm", "mv", "edit"):
        assert 4 not in [i for i, _ in t_mod._todo_pick_candidates(_mixed(t_mod), action)]


def test_todo_pick_candidates_labels_carry_state_and_id(t_mod):
    labels = dict((i, l) for i, l in t_mod._todo_pick_candidates(_mixed(t_mod), "rm"))
    assert labels[1] == "1  ◻ one"
    assert labels[2] == "2  ✓ two"


def test_todo_pick_candidates_accepts_aliases(t_mod):
    # the picker is reached via the canonical name, but be robust to either
    assert (t_mod._todo_pick_candidates(_mixed(t_mod), "x")
            == t_mod._todo_pick_candidates(_mixed(t_mod), "done"))


def test_todo_pick_candidates_empty_list(t_mod):
    assert t_mod._todo_pick_candidates(_fresh(t_mod), "done") == []


# ─── t todo — expiry and the bare-id action set ────────────────────────────────

def test_todo_purge_drops_only_stale_finished_items(t_mod):
    data = _add(t_mod, _fresh(t_mod), "open", "just done", "long done", "long gone")
    t_mod._todo_apply(data, "done", ["2"], now=1000)          # recent
    t_mod._todo_apply(data, "done", ["3"], now=1000)
    t_mod._todo_apply(data, "rm", ["4"], now=1000)
    data["items"][2]["done_at"] = 1000 - 8 * 86400            # backdate
    data["items"][3]["deleted_at"] = 1000 - 8 * 86400
    dropped = t_mod._todo_purge(data, now=1000)
    assert dropped == 2
    assert [i["id"] for i in data["items"]] == [1, 2]         # open + recent survive


def test_todo_purge_leaves_timestampless_rows_alone(t_mod):
    # Rows written before expiry existed have no done_at and must not vanish on a
    # technicality.
    data = _add(t_mod, _fresh(t_mod), "one")
    data["items"][0]["done"] = True
    data["items"][0]["done_at"] = None
    assert t_mod._todo_purge(data, now=10 ** 9) == 0
    assert len(data["items"]) == 1


def test_todo_purge_never_touches_open_items(t_mod):
    data = _add(t_mod, _fresh(t_mod), "a", "b")
    assert t_mod._todo_purge(data, now=10 ** 9) == 0


def test_todo_save_expires_on_write(t_mod, tmp_path):
    data = _add(t_mod, _fresh(t_mod), "one", "two")
    t_mod._todo_apply(data, "done", ["1"], now=100)
    data["items"][0]["done_at"] = 100 - 8 * 86400
    path = str(tmp_path / "k.json")
    t_mod._todo_save(path, data)
    assert [i["id"] for i in t_mod._todo_load(path)["items"]] == [2]


def test_todo_id_actions_offers_reopen_only_when_finished(t_mod):
    data = _add(t_mod, _fresh(t_mod), "one")
    item = data["items"][0]
    assert [a for a, _ in t_mod._todo_id_actions(item)] == ["done", "rm", "mv"]
    item["done"] = True
    assert [a for a, _ in t_mod._todo_id_actions(item)] == ["_reopen", "rm", "mv"]


# ─── t todo — actions widen to the same scope as the view ──────────────────────

def _two_lists(t_mod):
    a = _add(t_mod, _fresh(t_mod), "help")
    b = _add(t_mod, _fresh(t_mod), "other slot thing")
    return [("dotfiles-1", a), ("dotfiles-3", b)]


def test_todo_scoped_candidates_label_the_slot_when_several(t_mod):
    # The view widens to the repo, so the actions must too — otherwise you see an item
    # you cannot touch, which is what `t todo rm` did from a repo dir.
    got = t_mod._todo_scoped_candidates(_two_lists(t_mod), "rm")
    assert [(k, i) for k, i, _ in got] == [("dotfiles-1", 1), ("dotfiles-3", 1)]
    assert got[0][2].startswith("dotfiles-1")
    assert got[1][2].startswith("dotfiles-3")


def test_todo_scoped_candidates_drop_the_slot_column_for_one_list(t_mod):
    entries = _two_lists(t_mod)[:1]
    assert t_mod._todo_scoped_candidates(entries, "rm")[0][2] == "1  ◻ help"


def test_todo_scoped_candidates_stay_action_aware(t_mod):
    entries = _two_lists(t_mod)
    t_mod._todo_apply(entries[0][1], "done", ["1"], now=200)
    assert [k for k, _, _ in t_mod._todo_scoped_candidates(entries, "done")] == ["dotfiles-3"]


def test_todo_locate_finds_an_id_across_lists(t_mod):
    entries = _two_lists(t_mod)
    assert [k for k, _ in t_mod._todo_locate(entries, "1")] == ["dotfiles-1", "dotfiles-3"]
    assert t_mod._todo_locate(entries, "9") == []


def test_todo_locate_is_unambiguous_when_only_one_list_has_it(t_mod):
    entries = _two_lists(t_mod)
    t_mod._todo_apply(entries[1][1], "add", ["second"], now=100)
    hits = t_mod._todo_locate(entries, "2")
    assert len(hits) == 1 and hits[0][0] == "dotfiles-3"


def test_todo_locate_skips_tombstones(t_mod):
    entries = _two_lists(t_mod)
    t_mod._todo_apply(entries[0][1], "rm", ["1"], now=200)
    assert [k for k, _ in t_mod._todo_locate(entries, "1")] == ["dotfiles-3"]
