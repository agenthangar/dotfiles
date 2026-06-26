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
