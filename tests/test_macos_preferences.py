"""Exercise preferences with an in-memory defaults substitute, never cfprefsd."""
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import stat
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('macos_preferences', ROOT / 'lib/macos/apply.py')
macos = importlib.util.module_from_spec(spec)
spec.loader.exec_module(macos)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / '.config'))
    state, writes = {}, []

    def defaults(*args):
        command, domain = args[:2]
        if command == 'export':
            if domain not in state:
                return SimpleNamespace(returncode=1, stdout=b'', stderr=b'Domain does not exist')
            return SimpleNamespace(returncode=0, stdout=plistlib.dumps(state[domain]), stderr=b'')
        assert command == 'write'
        # Every write must already have a restrictive, complete backup.
        backup = list((tmp_path / '.config/dotfiles/backups').glob('*/managed-values.plist'))
        assert len(backup) == 1
        assert stat.S_IMODE(backup[0].stat().st_mode) == 0o600
        assert stat.S_IMODE(backup[0].parent.stat().st_mode) == 0o700
        key, xml = args[2:]
        value = plistlib.loads(xml.encode())
        state.setdefault(domain, {})[key] = value
        writes.append(args)
        return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')

    monkeypatch.setattr(macos, 'defaults', defaults)
    return tmp_path, state, writes


def test_apply_idempotent_and_preserves_unrelated_values(sandbox):
    home, state, writes = sandbox
    state['com.knollsoft.Rectangle'] = {'private-unmanaged': 'keep', 'leftHalf': {'keyCode': 1}}
    macos.apply(ROOT / 'lib/macos/preferences.json')
    assert state['com.knollsoft.Rectangle']['private-unmanaged'] == 'keep'
    assert state['com.knollsoft.Rectangle']['leftHalf'] == {'keyCode': 123, 'modifierFlags': 1835008}
    assert state['com.knollsoft.Rectangle']['almostMaximize'] == {}
    assert state['com.knollsoft.Rectangle']['wasWelcomeDisplayed'] is True
    assert type(state['com.knollsoft.Rectangle']['stageSize']) is float
    assert type(state['com.apple.dock']['tilesize']) is int
    assert state['com.apple.dock']['autohide'] is True
    backup = next((home / '.config/dotfiles/backups').glob('*/managed-values.plist'))
    saved = plistlib.loads(backup.read_bytes())
    assert saved['com.knollsoft.Rectangle']['leftHalf'] == {'present': True, 'value': {'keyCode': 1}}
    assert saved['com.apple.dock']['autohide'] == {'present': False}
    assert 'private-unmanaged' not in saved['com.knollsoft.Rectangle']
    count = len(writes)
    macos.apply(ROOT / 'lib/macos/preferences.json')
    assert len(writes) == count
    assert len(list(backup.parent.parent.iterdir())) == 1


@pytest.mark.parametrize('entry', [
    {'type': 'bool', 'value': 1}, {'type': 'int', 'value': True},
    {'type': 'dict', 'value': {'keyCode': True}}, {'type': 'unknown', 'value': 2},
    {'type': 'float', 'value': float('nan')}, {'type': [], 'value': 2},
])
def test_entire_manifest_validated_before_defaults(sandbox, monkeypatch, entry):
    home, _, writes = sandbox
    profile = home / 'bad.json'
    profile.write_text(json.dumps({'version': 1, 'domains': {
        'good': {'valid': {'type': 'bool', 'value': True}}, 'bad': {'invalid': entry}}}))
    monkeypatch.setattr(macos, 'defaults', lambda *args: pytest.fail('defaults must not be invoked'))
    with pytest.raises(macos.PreferenceError):
        macos.apply(profile)
    assert not writes
    assert not (home / '.config').exists()


def test_failed_write_reports_key_and_keeps_backup(sandbox, monkeypatch):
    home, _, _ = sandbox
    original = macos.defaults
    def fail(*args):
        if args[0] == 'write':
            return SimpleNamespace(returncode=1, stdout=b'', stderr=b'permission denied')
        return original(*args)
    monkeypatch.setattr(macos, 'defaults', fail)
    with pytest.raises(macos.PreferenceError, match='could not write com.knollsoft.Rectangle allowAnyShortcut'):
        macos.apply(ROOT / 'lib/macos/preferences.json')
    assert len(list((home / '.config/dotfiles/backups').glob('*/managed-values.plist'))) == 1


def test_failed_read_is_not_treated_as_empty(sandbox, monkeypatch):
    home, _, writes = sandbox
    monkeypatch.setattr(macos, 'defaults', lambda *args: SimpleNamespace(returncode=1, stdout=b'', stderr=b'permission denied'))
    with pytest.raises(macos.PreferenceError, match='could not read'):
        macos.apply(ROOT / 'lib/macos/preferences.json')
    assert not writes
    assert not (home / '.config').exists()


def test_backup_failure_prevents_writes(sandbox):
    home, _, writes = sandbox
    (home / '.config').write_text('blocked')
    with pytest.raises(OSError):
        macos.apply(ROOT / 'lib/macos/preferences.json')
    assert not writes


def test_skips_non_macos_and_optout(monkeypatch, capsys):
    monkeypatch.setattr(macos.sys, 'argv', ['apply.py'])
    monkeypatch.setattr(macos.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(macos, 'apply', lambda *args: pytest.fail('must skip'))
    assert macos.main() == 0
    assert 'DOTFILES_NO_MACOS=1' in capsys.readouterr().out
    monkeypatch.delenv('DOTFILES_NO_MACOS')
    monkeypatch.setattr(macos.platform, 'system', lambda: 'Linux')
    assert macos.main() == 0
    assert 'Linux' in capsys.readouterr().out
