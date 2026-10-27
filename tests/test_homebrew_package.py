"""Use full, Git-free Homebrew payloads and a fake Cellar/opt upgrade."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ['.zshrc', '.tmux.conf', '.zshrc.local.example', 'Brewfile',
           'install.sh', 'bin', 'lib', 'agents', 'ssh', 'launchd', 'scripts']


@pytest.fixture
def package(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    opt = tmp_path / 'opt with spaces' / 'dotfiles' / 'libexec'
    opt.parent.mkdir(parents=True)
    releases = []
    for version in ('1', '2'):
        release = tmp_path / 'Cellar' / version / 'libexec'
        release.mkdir(parents=True)
        for name in PAYLOAD:
            if (ROOT / name).is_dir():
                shutil.copytree(ROOT / name, release / name)
            else:
                shutil.copy2(ROOT / name, release / name)
        (release / '.dotfiles-package').write_text(str(opt) + '\n')
        (release / '.dotfiles-version').write_text(version + '\n')
        releases.append(release)
    opt.symlink_to(releases[0], target_is_directory=True)
    fake = tmp_path / 'fake-bin'
    fake.mkdir()
    (fake / 'git').write_text('#!/bin/sh\necho git >> "$CALL_LOG"\nexit 99\n')
    (fake / 'brew').write_text('''#!/bin/sh
printf 'brew %s\\n' "$*" >> "$CALL_LOG"
[ -z "$BREW_FAIL" ] || exit 42
ln -sfn "$NEXT_RELEASE" "$PACKAGE_OPT"
[ -z "$REMOVE_OLD" ] || rm -rf "$OLD_RELEASE"
''')
    for f in fake.iterdir():
        f.chmod(0o755)
    env = {**os.environ, 'HOME': str(home), 'XDG_CONFIG_HOME': str(home / '.config'),
           'PATH': str(fake) + os.pathsep + os.environ['PATH'],
           'DOTFILES_NO_T': '1', 'DOTFILES_NO_BREW': '1', 'DOTFILES_NO_TMUX': '1',
           'DOTFILES_NO_CLIP_BRIDGE': '1', 'DOTFILES_NO_SUDO': '1',
           'DOTFILES_LINKS_ONLY': '', 'PII_SCRUB_RULES': '',
           'CALL_LOG': str(tmp_path / 'calls'), 'NEXT_RELEASE': str(releases[1]),
           'OLD_RELEASE': str(releases[0]), 'PACKAGE_OPT': str(opt),
           'BREW_FAIL': '', 'REMOVE_OLD': ''}
    return home, opt, releases, env


def run(package, *args, env_extra=None):
    _, opt, _, env = package
    return subprocess.run([str(opt / 'bin/dots'), *args],
                          env={**env, **(env_extra or {})}, capture_output=True,
                          text=True, timeout=30)


def test_full_install_without_git_checkout(package):
    home, opt, _, env = package
    result = run(package)
    assert result.returncode == 0, result.stderr
    assert not (home / 'code').exists()
    assert not Path(env['CALL_LOG']).exists()
    assert not (home / '.zshrc').is_symlink()
    assert 'dotfiles Homebrew' in (home / '.zshrc').read_text()
    assert (home / 'bin/sleep-manager').readlink() == opt / 'bin/sleep-manager'
    assert (home / '.zshrc.local').exists()
    assert not (home / '.gitconfig').exists()


def test_migrate_existing_checkout_and_keep_local_settings(package):
    home, _, _, _ = package
    checkout = home / 'code/dotfiles'
    checkout.mkdir(parents=True)
    (checkout / '.zshrc').write_text('# uncommitted source change\n')
    (home / '.zshrc').symlink_to(checkout / '.zshrc')
    (home / '.zshrc.local').write_text('# private local settings\n')
    result = run(package, 'install')
    assert result.returncode == 0, result.stderr
    assert (checkout / '.zshrc').read_text() == '# uncommitted source change\n'
    assert (home / '.zshrc.before-homebrew').resolve() == checkout / '.zshrc'
    assert (home / '.zshrc.local').read_text() == '# private local settings\n'


def test_real_shell_settings_survive_repeat_install(package):
    home, _, _, _ = package
    (home / '.zshrc').write_text('export MY_SETTING=kept\n')
    for _ in range(2):
        result = run(package, 'install')
        assert result.returncode == 0, result.stderr
    text = (home / '.zshrc').read_text()
    assert 'export MY_SETTING=kept' in text
    assert text.count('# >>> dotfiles Homebrew >>>') == 1


def test_upgrade_executes_new_payload_after_old_cellar_removed(package):
    home, opt, releases, env = package
    assert run(package).returncode == 0
    result = run(package, env_extra={'REMOVE_OLD': '1'})
    assert result.returncode == 0, result.stderr
    assert 'Homebrew dotfiles applied — 2' in result.stdout
    assert not releases[0].exists()
    assert opt.resolve() == releases[1]
    assert (home / 'bin/dots').is_file()
    assert Path(env['CALL_LOG']).read_text() == 'brew upgrade agenthangar/tap/dotfiles\n'


def test_failed_brew_upgrade_is_reported_without_switching(package):
    _, opt, releases, _ = package
    assert run(package).returncode == 0
    result = run(package, 'update', env_extra={'BREW_FAIL': '1'})
    assert result.returncode == 42
    assert opt.resolve() == releases[0]


def test_relink_is_offline_even_on_fresh_account(package):
    home, _, _, env = package
    result = run(package, '--relink')
    assert result.returncode == 0, result.stderr
    assert not Path(env['CALL_LOG']).exists()
    assert not (home / '.zshrc.local').exists()


def test_packaged_entrypoint_ignores_legacy_shell_update_function(package):
    home, _, _, _ = package
    (home / '.zshrc').write_text('dots() { echo legacy-checkout-update; }\n')
    result = run(package)
    assert result.returncode == 0, result.stderr
    assert 'legacy-checkout-update' not in result.stdout
    assert 'Homebrew dotfiles applied' in result.stdout


def test_shell_function_and_help_work_without_git(package):
    home, opt, _, env = package
    assert run(package).returncode == 0
    result = subprocess.run(['zsh', '-f', '-c',
                             'source ~/.zshrc; dots --relink; nosleep --help; help; '
                             'nosleep config --show; '
                             'python3 "$_DOTS_SOURCE_ROOT/lib/nosleep_hotspot.py" validate --ssid=Test'],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert 'Homebrew dotfiles applied' in result.stdout
    assert 'nosleep' in result.stdout
    assert '--hotspot-after' in result.stdout and 'Hotspot fallback' in result.stdout
    assert (opt / 'lib/nosleep_hotspot.py').is_file()
    assert (opt / 'lib/nosleep-fallback.zsh').is_file()
    assert (home / '.zshrc').is_file()


def test_all_uses_configured_t_hosts(package, tmp_path):
    home, _, _, env = package
    assert run(package).returncode == 0
    config = home / '.config/t/config.sh'
    config.parent.mkdir(parents=True)
    config.write_text('REMOTE_HOSTS[configured]=example.invalid\n')
    ssh = tmp_path / 'ssh'
    ssh.write_text('#!/bin/sh\nprintf "%s\\n" "$*" > "$SSH_LOG"\n')
    ssh.chmod(0o755)
    log = tmp_path / 'ssh-log'
    result = run(package, '--all', env_extra={'DOTS_SYNC_SSH': str(ssh), 'SSH_LOG': str(log)})
    assert result.returncode == 0, result.stderr
    assert 'example.invalid zsh -lic dots' in log.read_text()
    assert 'configured:' in result.stdout


@pytest.mark.parametrize('args', [('install', '--help'), ('--help',), ('install', '/some/checkout')])
def test_help_and_invalid_destination_do_not_install(package, args):
    home, _, _, _ = package
    result = run(package, *args)
    assert result.returncode == (2 if args[-1] == '/some/checkout' else 0)
    assert not (home / '.zshrc').exists()


def test_relink_then_normal_run_completes_full_setup(package):
    home, _, _, env = package
    assert run(package, '--relink').returncode == 0
    assert not (home / '.config/dotfiles/installed').exists()
    assert run(package).returncode == 0
    assert (home / '.config/dotfiles/installed').exists()
    assert (home / '.zshrc.local').exists()
    assert not Path(env['CALL_LOG']).exists()


def test_symlinked_compatibility_command_uses_package(package, tmp_path):
    home, opt, _, env = package
    alias = tmp_path / 'dotfiles-setup'
    alias.symlink_to(opt / 'scripts/dotfiles-setup')
    result = subprocess.run([str(alias)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not (home / 'code').exists()
    assert (home / '.config/dotfiles/installed').exists()


def test_open_shell_tracks_package_upgrade(package):
    _, opt, _, env = package
    assert run(package).returncode == 0
    result = subprocess.run(['zsh', '-f', '-c', '''
source ~/.zshrc
print -r -- "before=$_DOTS_PACKAGE_VERSION"
brew upgrade agenthangar/tap/dotfiles
_dots_reload_if_moved
print -r -- "after=$_DOTS_PACKAGE_VERSION"
'''], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert 'before=1' in result.stdout
    assert 'after=2' in result.stdout
    assert opt.resolve().name == 'libexec'
