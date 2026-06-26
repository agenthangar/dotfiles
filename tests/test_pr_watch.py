"""Unit tests for the pure logic in bin/pr-watch.

The triage core (classify / failing_checks / pending_checks) gates an autonomous
agent that edits code and pushes on its own, so its decision matrix is the most
important thing in this repo to lock down. Subprocess/launchd/tmux orchestration
(spawn_fix, cmd_poll, notify, gh_json) is out of scope by design.
"""

import json


# ─── failing_checks ──────────────────────────────────────────────────────────────

def test_failing_checks_empty_and_none(prwatch_mod):
    assert prwatch_mod.failing_checks(None) == []
    assert prwatch_mod.failing_checks([]) == []


def test_failing_checks_checkrun_conclusions(prwatch_mod):
    rollup = [
        {"name": "a", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "b", "status": "COMPLETED", "conclusion": "FAILURE"},
        {"name": "c", "status": "COMPLETED", "conclusion": "TIMED_OUT"},
        {"name": "d", "status": "COMPLETED", "conclusion": "CANCELLED"},
        {"name": "e", "status": "COMPLETED", "conclusion": "ACTION_REQUIRED"},
    ]
    assert prwatch_mod.failing_checks(rollup) == ["b", "c", "d", "e"]


def test_failing_checks_statuscontext_state(prwatch_mod):
    rollup = [
        {"context": "ci/x", "state": "SUCCESS"},
        {"context": "ci/y", "state": "FAILURE"},
        {"context": "ci/z", "state": "ERROR"},
    ]
    assert prwatch_mod.failing_checks(rollup) == ["ci/y", "ci/z"]


def test_failing_checks_in_progress_is_not_failure(prwatch_mod):
    rollup = [{"name": "a", "status": "IN_PROGRESS", "conclusion": None}]
    assert prwatch_mod.failing_checks(rollup) == []


def test_failing_checks_unnamed_check_fallback(prwatch_mod):
    assert prwatch_mod.failing_checks([{"conclusion": "FAILURE"}]) == ["check"]


# ─── pending_checks ──────────────────────────────────────────────────────────────

def test_pending_checks_checkrun_status(prwatch_mod):
    rollup = [
        {"name": "a", "status": "COMPLETED"},
        {"name": "b", "status": "IN_PROGRESS"},
        {"name": "c", "status": "QUEUED"},
    ]
    assert prwatch_mod.pending_checks(rollup) == ["b", "c"]


def test_pending_checks_statuscontext_state(prwatch_mod):
    rollup = [
        {"context": "x", "state": "PENDING"},
        {"context": "y", "state": "EXPECTED"},
        {"context": "z", "state": "SUCCESS"},
    ]
    assert prwatch_mod.pending_checks(rollup) == ["x", "y"]


def test_pending_checks_none(prwatch_mod):
    assert prwatch_mod.pending_checks(None) == []


# ─── classify (the decision matrix) ──────────────────────────────────────────────

def test_classify_draft_skipped(prwatch_mod):
    assert prwatch_mod.classify({"isDraft": True}) == (None, None)


def test_classify_cross_repo_skipped(prwatch_mod):
    assert prwatch_mod.classify({"isCrossRepository": True}) == (None, None)


def test_classify_conflicting_mergeable(prwatch_mod):
    reason, detail = prwatch_mod.classify({"mergeable": "CONFLICTING", "baseRefName": "main"})
    assert detail == "conflict"
    assert "merge conflict with main" == reason


def test_classify_dirty_merge_state(prwatch_mod):
    reason, detail = prwatch_mod.classify({"mergeStateStatus": "DIRTY"})
    assert detail == "conflict"
    assert "merge conflict with main" in reason   # base defaults to main


def test_classify_failing_ci_no_pending(prwatch_mod):
    pr = {
        "statusCheckRollup": [
            {"name": "build", "status": "COMPLETED", "conclusion": "FAILURE"},
        ],
    }
    reason, detail = prwatch_mod.classify(pr)
    assert reason == "failing CI: build"
    assert detail == "ci:build"


def test_classify_failing_ci_with_pending_waits(prwatch_mod):
    # A stale failure alongside a still-running check must NOT trigger a fix.
    pr = {
        "statusCheckRollup": [
            {"name": "old", "status": "COMPLETED", "conclusion": "FAILURE"},
            {"name": "new", "status": "IN_PROGRESS", "conclusion": None},
        ],
    }
    assert prwatch_mod.classify(pr) == (None, None)


def test_classify_clean_pr(prwatch_mod):
    pr = {
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [
            {"name": "build", "status": "COMPLETED", "conclusion": "SUCCESS"},
        ],
    }
    assert prwatch_mod.classify(pr) == (None, None)


# ─── _load_config_file ───────────────────────────────────────────────────────────

def test_load_config_file_sets_unset_keys(prwatch_mod, tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.write_text("\n".join([
        "# a comment",
        "",
        "PR_WATCH_REPO=owner/repo",
        "malformed line no equals",
        "ALREADY_SET=fromfile",
    ]))
    monkeypatch.setattr(prwatch_mod, "CONFIG_FILE", str(cfg))
    monkeypatch.setenv("ALREADY_SET", "fromenv")
    monkeypatch.delenv("PR_WATCH_REPO", raising=False)
    prwatch_mod._load_config_file()
    import os
    assert os.environ["PR_WATCH_REPO"] == "owner/repo"   # unset key populated
    assert os.environ["ALREADY_SET"] == "fromenv"        # existing env wins


def test_load_config_file_missing_is_noop(prwatch_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(prwatch_mod, "CONFIG_FILE", str(tmp_path / "absent"))
    prwatch_mod._load_config_file()   # must not raise


# ─── load_state / save_state ─────────────────────────────────────────────────────

def test_state_round_trip(prwatch_mod, tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_file = state_dir / "state.json"
    monkeypatch.setattr(prwatch_mod, "STATE_DIR", str(state_dir))
    monkeypatch.setattr(prwatch_mod, "STATE_FILE", str(state_file))
    payload = {"acted": {"5": "sha"}, "attempts": {"5": 1}, "gaveup": {}}
    prwatch_mod.save_state(payload)
    assert json.loads(state_file.read_text()) == payload
    assert prwatch_mod.load_state() == payload


def test_load_state_missing_returns_default(prwatch_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(prwatch_mod, "STATE_FILE", str(tmp_path / "absent.json"))
    assert prwatch_mod.load_state() == {"acted": {}, "attempts": {}, "gaveup": {}}


def test_load_state_corrupt_returns_default(prwatch_mod, tmp_path, monkeypatch):
    bad = tmp_path / "state.json"
    bad.write_text("{ not json")
    monkeypatch.setattr(prwatch_mod, "STATE_FILE", str(bad))
    assert prwatch_mod.load_state() == {"acted": {}, "attempts": {}, "gaveup": {}}


# ─── write_brief ─────────────────────────────────────────────────────────────────

def test_write_brief_conflict(prwatch_mod, tmp_path, monkeypatch):
    # Conflict path takes no gh subprocess call, so it stays pure.
    tasks = tmp_path / "tasks"
    monkeypatch.setattr(prwatch_mod, "TASKS_DIR", str(tasks))
    pr = {
        "number": 42,
        "headRefName": "dev/feature-7",
        "headRefOid": "abcdef0123456789",
        "baseRefName": "main",
    }
    path = prwatch_mod.write_brief(pr, "merge conflict with main", "conflict")
    text = open(path).read()
    assert path.endswith("pr42.md")
    assert "PR #42" in text
    assert "merge conflict with main" in text
    assert "dev/feature-7" in text
    assert "git merge origin/main" in text   # the conflict-specific fix step
