"""Exercise the Homebrew entry point with real Git clones in a sandbox HOME."""

import os
from pathlib import Path
import subprocess

import pytest

from test_install_migration import _seed_worktree

ROOT = Path(__file__).resolve().parent.parent
SETUP = ROOT / "bin/dots"
UPSTREAM = "https://github.com/agenthangar/dotfiles.git"


@pytest.fixture
def sandbox(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    source = tmp_path / "source"
    _seed_worktree(source)
    env = {
        **os.environ,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": f"url.{source}.insteadOf",
        "GIT_CONFIG_VALUE_0": UPSTREAM,
        "GIT_CONFIG_KEY_1": "user.name",
        "GIT_CONFIG_VALUE_1": "Test",
        "GIT_CONFIG_KEY_2": "user.email",
        "GIT_CONFIG_VALUE_2": "test@example.com",
        "DOTFILES_LINKS_ONLY": "1",
        "DOTFILES_NO_T": "1",
        "DOTFILES_NO_TMUX": "1",
    }

    def git(*args):
        return subprocess.run(
            ["git", *map(str, args)], env=env, check=True,
            capture_output=True, text=True,
        )

    git("init", "-b", "main", source)
    git("-C", source, "add", ".")
    git("-C", source, "-c", "core.hooksPath=/dev/null", "commit", "-m", "fixture")
    return home, source, env, git


def run_setup(env, *args):
    return subprocess.run([str(SETUP), "install", *map(str, args)], env=env, capture_output=True, text=True)


def test_fresh_install_and_repeat_preserve_local_config(sandbox):
    home, _, env, _ = sandbox
    (home / ".zshrc").write_text("original shell\n")
    local = home / ".zshrc.local"
    local.write_text("# private config\n")
    for _ in range(2):
        result = run_setup(env)
        assert result.returncode == 0, result.stderr
        assert (home / ".zshrc").resolve() == home / "code/dotfiles/.zshrc"
        assert local.read_text() == "# private config\n"
    assert (home / ".zshrc.bak").read_text() == "original shell\n"


def test_custom_directory_with_spaces(sandbox):
    home, _, env, _ = sandbox
    destination = home / "custom checkout"
    result = run_setup(env, destination)
    assert result.returncode == 0, result.stderr
    assert (home / ".zshrc").resolve() == destination / ".zshrc"


@pytest.mark.parametrize("problem", ["directory", "file", "symlink", "origin", "branch", "dirty", "worktree"])
def test_refuse_incompatible_destination(sandbox, problem):
    home, source, env, git = sandbox
    destination = home / "destination"
    if problem == "directory":
        destination.mkdir()
        (destination / "keep").write_text("keep")
    elif problem == "file":
        destination.write_text("keep")
    elif problem == "symlink":
        destination.symlink_to(home / "missing")
    elif problem == "worktree":
        git("-C", source, "worktree", "add", "-b", "dev/test", destination)
    else:
        git("clone", UPSTREAM, destination)
        if problem == "origin":
            git("-C", destination, "remote", "set-url", "origin", "https://example.com/other.git")
        elif problem == "branch":
            git("-C", destination, "switch", "-c", "dev/test")
        elif problem == "dirty":
            (destination / ".zshrc").write_text("keep local edits")
    result = run_setup(env, destination)
    assert result.returncode == 1
    assert "dots install:" in result.stderr
    assert not (home / ".zshrc").exists()
    if problem == "dirty":
        assert (destination / ".zshrc").read_text() == "keep local edits"


def test_help_and_invalid_arguments_do_not_install(sandbox):
    home, _, env, _ = sandbox
    assert "Usage:" in run_setup(env, "--help").stdout
    assert run_setup(env, "--unknown").returncode == 2
    assert run_setup(env, "one", "two").returncode == 2
    assert not (home / "code").exists()


def test_installer_failure_is_propagated(sandbox):
    home, source, env, git = sandbox
    (source / "install.sh").write_text("#!/bin/bash\nexit 42\n")
    git("-C", source, "add", "install.sh")
    git("-C", source, "-c", "core.hooksPath=/dev/null", "commit", "-m", "failing installer")
    assert run_setup(env).returncode == 42
    assert not (home / ".zshrc").exists()


def test_legacy_alias_and_symlinked_command(sandbox):
    home, _, env, _ = sandbox
    legacy = subprocess.run(
        [str(ROOT / "scripts/dotfiles-setup"), "--help"],
        env=env, capture_output=True, text=True,
    )
    assert legacy.returncode == 0
    assert "Usage: dots install" in legacy.stdout
    linked = home / "dots"
    linked.symlink_to(SETUP)
    result = subprocess.run([str(linked), "install"], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (home / "bin/dots").resolve() == home / "code/dotfiles/bin/dots"


def test_update_before_install_fails_without_recursing(sandbox):
    _, _, env, _ = sandbox
    result = subprocess.run(
        [str(SETUP), "update"], env=env, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 1
    assert "run dots install first" in result.stderr


def test_executable_update_forwards_arguments_and_status(sandbox):
    home, _, env, _ = sandbox
    (home / ".zshrc").write_text('dots() { print -r -- "args=$*"; return 17; }\n')
    result = subprocess.run(
        [str(SETUP), "update", "--relink"], env=env, capture_output=True, text=True,
    )
    assert result.returncode == 17
    assert "args=--relink" in result.stdout


def shell_dots(env, *args):
    return subprocess.run(
        ["zsh", "-f", "-c", 'source "$1"; shift; dots "$@"',
         "test", str(ROOT / ".zshrc"), *args],
        env=env, capture_output=True, text=True, timeout=30,
    )


def test_existing_zsh_function_dispatches_install(sandbox):
    home, _, env, _ = sandbox
    result = shell_dots(env, "install")
    assert result.returncode == 0, result.stderr
    assert (home / ".zshrc").resolve() == home / "code/dotfiles/.zshrc"


@pytest.mark.parametrize("args", [("update", "--relink"), ("--relink",)])
def test_shell_update_and_legacy_flags_reconcile_links(sandbox, args):
    home, _, env, _ = sandbox
    assert run_setup(env).returncode == 0
    (home / "bin/csync").unlink()
    result = shell_dots(env, *args)
    assert result.returncode == 0, result.stderr
    assert (home / "bin/csync").is_symlink()


@pytest.mark.parametrize("args", [("--help",), ("install", "--help"), ("update", "--help")])
def test_help_works_before_and_after_shell_integration(sandbox, args):
    _, _, env, _ = sandbox
    for result in (
        subprocess.run([str(SETUP), *args], env=env, capture_output=True, text=True),
        shell_dots(env, *args),
    ):
        assert result.returncode == 0, result.stderr
        assert "Usage: dots" in result.stdout


def test_unknown_command_is_not_treated_as_update(sandbox):
    home, _, env, _ = sandbox
    for result in (
        subprocess.run([str(SETUP), "typo"], env=env, capture_output=True, text=True),
        shell_dots(env, "typo"),
    ):
        assert result.returncode == 2
        assert "unknown command" in result.stderr
    assert not (home / "code").exists()


def test_bare_dots_sets_up_fresh_account(sandbox):
    home, _, env, _ = sandbox
    result = subprocess.run([str(SETUP)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (home / ".zshrc").resolve() == home / "code/dotfiles/.zshrc"


def test_bare_symlinked_dots_uses_shared_installer(sandbox):
    home, _, env, _ = sandbox
    linked = home / "brew-dots"
    linked.symlink_to(SETUP)
    result = subprocess.run([str(linked)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (home / "bin/dots").is_symlink()


def test_bare_dots_updates_installed_account(sandbox):
    home, _, env, _ = sandbox
    (home / ".zshrc").write_text('dots() { print -r -- "updated:$#"; return 17; }\n')
    result = subprocess.run([str(SETUP)], env=env, capture_output=True, text=True)
    assert result.returncode == 17
    assert "updated:0" in result.stdout
    assert not (home / "code").exists()


def test_bare_dots_preserves_installer_safety_checks(sandbox):
    home, _, env, _ = sandbox
    destination = home / "code/dotfiles"
    destination.mkdir(parents=True)
    (destination / "keep").write_text("local data")
    result = subprocess.run([str(SETUP)], env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert "canonical Git clone" in result.stderr
    assert (destination / "keep").read_text() == "local data"
    assert not (home / ".zshrc").exists()
