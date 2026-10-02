"""The staged split must never let dots reclaim or orphan standalone t links."""

import os
from pathlib import Path
import subprocess

from test_install_migration import _seed_worktree, git, run_install


def checkout(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    repo = home / "code" / "dotfiles"
    _seed_worktree(repo)
    (repo / "bin" / "t").write_text("#!/bin/sh\n")
    git("init", "-q", "-b", "main", cwd=repo)
    git("config", "user.name", "T", cwd=repo)
    git("config", "user.email", "t@example.test", cwd=repo)
    git("add", ".", cwd=repo)
    git("commit", "-qm", "seed", cwd=repo)
    return home, repo


def standalone(home):
    repo = home / "code" / "t"
    (repo / "bin").mkdir(parents=True)
    (repo / ".t-install-version").write_text("1\n")
    (repo / "t.plugin.zsh").write_text("# plugin\n")
    for name in ("t", "claude-stamp-tmux", "cursor-beam"):
        p = repo / "bin" / name
        p.write_text("#!/bin/sh\n")
        p.chmod(0o755)
    p = repo / "install.sh"
    p.write_text('''#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
mkdir -p "$HOME/bin"
for name in t claude-stamp-tmux cursor-beam; do
    ln -sfn "$root/bin/$name" "$HOME/bin/$name"
done
printf '%s\\n' "$T_LOCAL_RC" "$T_PERMISSIONS_DIR" "$T_AUTO_TRUST" > "$root/adapter.txt"
''')
    p.chmod(0o755)
    return repo


def release_stub(tmp_path, home):
    """A local curl replacement that serves a tiny checkout-free release installer."""
    template = standalone(tmp_path / "template")
    bindir = tmp_path / "fake-bin"
    bindir.mkdir()
    curl = bindir / "curl"
    curl.write_text('''#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$T_TEST_CURL_LOG"
while [ "$#" -gt 0 ]; do
    if [ "$1" = -o ]; then shift; output=$1; break; fi
    shift
done
cp "$T_TEST_BOOTSTRAP" "$output"
''')
    curl.chmod(0o755)
    bootstrap = tmp_path / "fake-release.py"
    bootstrap.write_text('''import os
from pathlib import Path
import shutil
import subprocess
root = Path(os.environ["HOME"]) / ".local/share/t/releases/v1.0.0"
shutil.copytree(os.environ["T_TEST_RELEASE_TEMPLATE"], root)
subprocess.run([str(root / "install.sh")], check=True)
''')
    env = {
        "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
        "T_TEST_CURL_LOG": str(tmp_path / "curl.log"),
        "T_TEST_BOOTSTRAP": str(bootstrap),
        "T_TEST_RELEASE_TEMPLATE": str(template),
    }
    return env, home / ".local/share/t/releases/v1.0.0"


def test_repeated_dotfiles_relink_keeps_standalone_ownership(tmp_path):
    home, repo = checkout(tmp_path)
    t = standalone(home)
    for _ in range(2):
        r = run_install(repo, home, DOTFILES_LINKS_ONLY="1", DOTFILES_NO_T="")
        assert r.returncode == 0, r.stderr
        for name in ("t", "claude-stamp-tmux", "cursor-beam"):
            assert (home / "bin" / name).resolve() == t / "bin" / name
    assert (t / "adapter.txt").read_text().splitlines() == [
        str(home / ".zshrc.local"), str(repo / "agents"), "1"
    ]


def test_invalid_standalone_marker_stops_offline_relink(tmp_path):
    home, repo = checkout(tmp_path)
    t = standalone(home)
    (t / ".t-install-version").write_text("99\n")
    r = run_install(repo, home, DOTFILES_LINKS_ONLY="1", DOTFILES_NO_T="")
    assert r.returncode != 0
    assert "standalone t is missing" in r.stderr
    assert not (home / "bin" / "t").exists()
    assert not (t / "adapter.txt").exists()


def preflight(home, repo, **extra_env):
    env = {**os.environ, "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")}
    env.update(extra_env)
    return subprocess.run(
        ["bash", "-c", 'source "$1/lib/t-integration.sh"; _dots_t_preflight "$1"', "_", str(repo)],
        env=env, capture_output=True, text=True,
    )


def removal_ref(repo):
    git("checkout", "-qb", "removal", cwd=repo)
    git("rm", "bin/t", cwd=repo)
    git("commit", "-qm", "remove bundled t", cwd=repo)
    git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=repo)
    git("checkout", "-q", "main", cwd=repo)


def test_removal_preflight_stops_before_old_executable_disappears(tmp_path):
    home, repo = checkout(tmp_path)
    removal_ref(repo)
    bindir = tmp_path / "fake-bin"
    bindir.mkdir()
    curl = bindir / "curl"
    curl.write_text("#!/bin/sh\nexit 22\n")
    curl.chmod(0o755)
    r = preflight(home, repo, PATH=str(bindir) + os.pathsep + os.environ["PATH"])
    assert r.returncode == 1
    assert "release installation failed" in r.stderr
    assert (repo / "bin" / "t").exists()
    assert git("branch", "--show-current", cwd=repo).stdout.strip() == "main"


def test_removal_preflight_relinks_standalone_before_fast_forward(tmp_path):
    home, repo = checkout(tmp_path)
    t = standalone(home)
    removal_ref(repo)
    r = preflight(home, repo)
    assert r.returncode == 0, r.stderr
    git("merge", "--ff-only", "origin/main", cwd=repo)
    assert not (repo / "bin" / "t").exists()
    assert (home / "bin" / "t").resolve() == t / "bin" / "t"
    assert (home / "bin" / "t").exists()


def test_removal_preflight_bootstraps_release_without_checkout(tmp_path):
    home, repo = checkout(tmp_path)
    removal_ref(repo)
    env, release = release_stub(tmp_path, home)
    r = preflight(home, repo, **env)
    assert r.returncode == 0, r.stderr
    assert (home / "bin/t").resolve() == release / "bin/t"
    assert not (home / "code/t").exists()
    assert len((tmp_path / "curl.log").read_text().splitlines()) == 1


def test_old_owned_link_migrates_during_links_only_relink(tmp_path):
    home, repo = checkout(tmp_path)
    (home / "bin").mkdir()
    (home / "bin/t").symlink_to(repo / "bin/t")
    (repo / "bin/t").unlink()  # old dots already fast-forwarded
    env, release = release_stub(tmp_path, home)
    r = run_install(repo, home, DOTFILES_LINKS_ONLY="1", DOTFILES_NO_T="", **env)
    assert r.returncode == 0, r.stderr
    assert (home / "bin/t").resolve() == release / "bin/t"
    again = run_install(repo, home, DOTFILES_LINKS_ONLY="1", DOTFILES_NO_T="", **env)
    assert again.returncode == 0, again.stderr
    assert len((tmp_path / "curl.log").read_text().splitlines()) == 1


def test_links_only_without_owned_link_stays_offline(tmp_path):
    home, repo = checkout(tmp_path)
    env, _ = release_stub(tmp_path, home)
    r = run_install(repo, home, DOTFILES_LINKS_ONLY="1", DOTFILES_NO_T="", **env)
    assert r.returncode != 0
    assert not (tmp_path / "curl.log").exists()


def test_explicit_invalid_t_home_blocks_legacy_bootstrap(tmp_path):
    home, repo = checkout(tmp_path)
    (home / "bin").mkdir()
    (home / "bin/t").symlink_to(repo / "bin/t")
    (repo / "bin/t").unlink()
    env, _ = release_stub(tmp_path, home)
    r = run_install(repo, home, DOTFILES_LINKS_ONLY="1", DOTFILES_NO_T="",
                    DOTFILES_T_HOME=str(home / "custom/t"), **env)
    assert r.returncode != 0
    assert not (tmp_path / "curl.log").exists()


def test_full_install_uses_release_without_creating_git_checkout(tmp_path):
    home, repo = checkout(tmp_path)
    env, release = release_stub(tmp_path, home)
    r = run_install(repo, home, DOTFILES_NO_T="", **env)
    assert r.returncode == 0, r.stderr
    assert (home / "bin/t").resolve() == release / "bin/t"
    assert not (home / "code/t").exists()


def test_opt_out_never_downloads_even_with_legacy_link(tmp_path):
    home, repo = checkout(tmp_path)
    (home / "bin").mkdir()
    (home / "bin/t").symlink_to(repo / "bin/t")
    (repo / "bin/t").unlink()
    env, _ = release_stub(tmp_path, home)
    r = run_install(repo, home, DOTFILES_LINKS_ONLY="1", DOTFILES_NO_T="1", **env)
    assert r.returncode == 0, r.stderr
    assert not (tmp_path / "curl.log").exists()


def test_failed_download_leaves_legacy_link_and_reports_failure(tmp_path):
    home, repo = checkout(tmp_path)
    (home / "bin").mkdir()
    (home / "bin/t").symlink_to(repo / "bin/t")
    (repo / "bin/t").unlink()
    bindir = tmp_path / "fake-bin"
    bindir.mkdir()
    curl = bindir / "curl"
    curl.write_text("#!/bin/sh\nexit 22\n")
    curl.chmod(0o755)
    r = run_install(repo, home, DOTFILES_LINKS_ONLY="1", DOTFILES_NO_T="",
                    PATH=str(bindir) + os.pathsep + os.environ["PATH"])
    assert r.returncode != 0
    assert "release installation failed" in r.stderr
    assert (home / "bin/t").is_symlink()
    assert (home / "bin/t").resolve() == repo / "bin/t"


def test_failed_release_validation_leaves_legacy_link(tmp_path):
    home, repo = checkout(tmp_path)
    (home / "bin").mkdir()
    (home / "bin/t").symlink_to(repo / "bin/t")
    (repo / "bin/t").unlink()
    env, _ = release_stub(tmp_path, home)
    Path(env["T_TEST_BOOTSTRAP"]).write_text("raise ValueError('bad release checksum')\n")
    r = run_install(repo, home, DOTFILES_LINKS_ONLY="1", DOTFILES_NO_T="", **env)
    assert r.returncode != 0
    assert "release installation failed" in r.stderr
    assert (home / "bin/t").is_symlink()
    assert not (home / ".local/share/t/releases/v1.0.0").exists()
