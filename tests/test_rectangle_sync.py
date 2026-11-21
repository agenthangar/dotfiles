"""Publisher tests use fake GitHub/defaults and sandbox HOME; no live services."""
import copy
import importlib.util
import json
from pathlib import Path
import plistlib
import stat
import subprocess
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('rectangle_sync', ROOT / 'lib/macos/rectangle_sync.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)

A, B, C = 'a' * 40, 'b' * 40, 'c' * 40
FORMULA = f'''class Dotfiles < Formula
  url "https://github.com/agenthangar/dotfiles/archive/{A}.tar.gz"
  version "0.11.9"
  sha256 "{'1' * 64}"
end
'''


class GitHub:
    def __init__(self, profile):
        self.profile = profile
        self.source_head = A
        self.tap_head = A
        self.formula = FORMULA
        self.prs = {}
        self.calls = []
        self.done = False
        self.checked = True
        self.crash = False
        self.closed = []

    def close_pending(self, repo, branch):
        self.closed.append((repo, branch))

    def head(self, repo):
        return self.source_head if repo == sync.SOURCE else self.tap_head

    def file(self, repo, path, ref):
        if repo == sync.TAP:
            return self.formula
        return self.profile

    def ensure_pr(self, repo, branch, base, path, content, title, body):
        self.calls.append((repo, branch, content))
        if branch not in self.prs:
            self.prs[branch] = len(self.prs) + 1
        if self.crash:
            self.crash = False
            raise sync.SyncError('simulated lost API response')
        if self.done:
            if repo == sync.SOURCE:
                self.profile = content
                self.source_head = B
            else:
                self.formula = content
                self.tap_head = C
        return self.prs[branch]

    def merged(self, repo, number):
        return (B if repo == sync.SOURCE else C) if self.done else None

    def contains(self, ancestor, descendant):
        return ancestor == descendant or (ancestor == A and descendant in (B, C))

    def ci(self, repo, sha):
        return self.checked

    def branch_head(self, repo, branch):
        return None


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(sync.time, 'sleep', lambda seconds: None)
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / '.config'))
    profile = (ROOT / sync.PROFILE).read_text()
    github = GitHub(profile)
    schema = sync.managed(sync.load_profile(profile))
    current = sync.upstream_values(schema)
    current.pop('almostMaximize')  # Ordinary Rectangle defaults omit disabled shortcuts.
    monkeypatch.setattr(sync.prefs, 'read_domain', lambda _: copy.deepcopy(current))
    publisher = sync.Publisher(github, tmp_path / 'private')
    monkeypatch.setattr(sync, 'archive_sha', lambda sha: '2' * 64)
    return tmp_path, current, github, publisher


def state(publisher):
    return json.loads(publisher.state_path.read_text())


def test_baseline_noop_metadata_and_missing_shortcut(sandbox):
    home, current, github, publisher = sandbox
    current['private-token'] = 'never capture'
    current['wasWelcomeDisplayed'] = False
    current['landscapeSnapAreas'] = 'historical difference'
    assert 'historical' in publisher.run()
    original = publisher.state_path.read_bytes()
    assert state(publisher)['baseline']['local']['almostMaximize'] == sync.MISSING
    assert 'wasWelcomeDisplayed' not in state(publisher)['baseline']['local']
    assert 'private-token' not in publisher.state_path.read_text()
    current['wasWelcomeDisplayed'] = True
    assert publisher.run() == 'no Rectangle changes'
    assert publisher.state_path.read_bytes() == original
    assert not github.calls
    assert stat.S_IMODE(publisher.directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(publisher.state_path.stat().st_mode) == 0o600


@pytest.mark.parametrize('kind', ['missing', 'bad_type', 'bad_dict', 'nonfinite'])
def test_invalid_capture_stops_before_mutation(sandbox, kind):
    _, current, github, publisher = sandbox
    if kind == 'missing':
        del current['launchOnLogin']
    elif kind == 'bad_type':
        current['launchOnLogin'] = 1
    elif kind == 'bad_dict':
        current['leftHalf'] = {'keyCode': True}
    else:
        current['stageSize'] = float('nan')
    with pytest.raises(sync.SyncError):
        publisher.run()
    assert not github.calls
    assert not publisher.state_path.exists()


def test_three_way_independent_preserves_upstream_and_domains(sandbox):
    _, current, github, publisher = sandbox
    publisher.run()
    current['leftHalf'] = {'keyCode': 12}
    profile = json.loads(github.profile)
    profile['domains'][sync.DOMAIN]['rightHalf']['value'] = {'keyCode': 13}
    github.profile = json.dumps(profile)
    assert 'pending' in publisher.run()
    payload = json.loads(github.calls[0][2])
    assert payload['domains'][sync.DOMAIN]['leftHalf']['value'] == {'keyCode': 12}
    assert payload['domains'][sync.DOMAIN]['rightHalf']['value'] == {'keyCode': 13}
    expected = copy.deepcopy(profile)
    expected['domains'][sync.DOMAIN]['leftHalf']['value'] = {'keyCode': 12}
    assert payload == expected


def test_same_key_conflict_and_convergence(sandbox):
    _, current, github, publisher = sandbox
    publisher.run()
    current['leftHalf'] = {'keyCode': 12}
    profile = json.loads(github.profile)
    profile['domains'][sync.DOMAIN]['leftHalf']['value'] = {'keyCode': 13}
    github.profile = json.dumps(profile)
    with pytest.raises(sync.SyncError, match='conflict: leftHalf'):
        publisher.run()
    assert not github.calls
    current['leftHalf'] = {'keyCode': 13}
    assert publisher.run() == 'no Rectangle changes'
    assert not github.calls


def test_actual_shortcut_deletion_maps_to_disabled(sandbox):
    _, current, github, publisher = sandbox
    publisher.run()
    del current['leftHalf']
    publisher.run()
    assert json.loads(github.calls[0][2])['domains'][sync.DOMAIN]['leftHalf']['value'] == {}


def test_snapshot_changes_stop_before_operation(sandbox, monkeypatch):
    _, current, github, publisher = sandbox
    calls = []
    def read(_):
        calls.append(1)
        values = copy.deepcopy(current)
        values['stackBadge'] = len(calls)
        return values
    monkeypatch.setattr(sync.prefs, 'read_domain', read)
    with pytest.raises(sync.SyncError, match='during capture'):
        publisher.run()
    assert not github.calls


def test_resume_crash_reuses_deterministic_branch_and_pending_capture(sandbox):
    _, current, github, publisher = sandbox
    publisher.run()
    current['leftHalf'] = {'keyCode': 12}
    github.crash = True
    with pytest.raises(sync.SyncError, match='lost API'):
        publisher.run()
    operation = state(publisher)['operation']
    assert 'source_pr' not in operation  # Persisted before the interrupted API mutation.
    current['rightHalf'] = {'keyCode': 99}  # Deferred until operation completes.
    assert 'pending' in publisher.run()
    assert len(github.prs) == 1
    assert github.calls[0][1] == github.calls[1][1]
    assert state(publisher)['operation']['local']['rightHalf']['value'] != {'keyCode': 99}
    count = len(github.calls)
    publisher.run()
    assert len(github.calls) == count


def test_source_then_patch_release_then_baseline(sandbox):
    _, current, github, publisher = sandbox
    publisher.run()
    current['leftHalf'] = {'keyCode': 12}
    github.done = True
    assert 'released' in publisher.run()
    assert [call[0] for call in github.calls] == [sync.SOURCE, sync.TAP]
    assert '  version "0.11.10"' in github.formula
    assert '/archive/' + B in github.formula
    assert '2' * 64 in github.formula
    assert 'operation' not in state(publisher)
    assert publisher.run() == 'no Rectangle changes'


def test_newer_release_already_contains_capture_never_rolls_back(sandbox):
    _, current, github, publisher = sandbox
    publisher.run()
    current['leftHalf'] = {'keyCode': 12}
    github.done = True
    github.formula = FORMULA.replace(A, B)
    assert 'released' in publisher.run()
    assert len(github.calls) == 1


def test_newer_source_main_is_released_and_requires_ci(sandbox, monkeypatch):
    _, current, github, publisher = sandbox
    publisher.run()
    current['leftHalf'] = {'keyCode': 12}
    publisher.run()
    github.done = True
    github.source_head = C
    github.contains = lambda ancestor, descendant: ancestor == descendant or (ancestor, descendant) in ((A, B), (B, C))
    github.checked = False
    assert 'CI pending' in publisher.run()
    assert not state(publisher)['operation'].get('tap')
    github.checked = True
    assert 'released' in publisher.run()
    assert '/archive/' + C in github.formula


def test_failed_ci_keeps_pending_operation(sandbox, monkeypatch):
    _, current, github, publisher = sandbox
    publisher.run()
    current['leftHalf'] = {'keyCode': 12}
    publisher.run()
    monkeypatch.setattr(github, 'merged', lambda *args: (_ for _ in ()).throw(sync.SyncError('CI failed')))
    with pytest.raises(sync.SyncError, match='CI failed'):
        publisher.run()
    assert 'operation' in state(publisher)
    assert len(github.calls) == 1


def test_tap_advancing_before_mutation_recalculates(sandbox, monkeypatch):
    _, current, github, publisher = sandbox
    publisher.run()
    current['leftHalf'] = {'keyCode': 12}
    publisher.run()
    github.done = True
    github.source_head = B
    original = sync.save
    def save_and_advance(path, value):
        original(path, value)
        if value.get('operation', {}).get('tap'):
            github.tap_head = C
    monkeypatch.setattr(sync, 'save', save_and_advance)
    assert 'recalculate' in publisher.run()
    assert 'tap' not in state(publisher)['operation']
    assert len(github.calls) == 1


def test_exclusive_lock(sandbox):
    _, _, _, publisher = sandbox
    with sync.locked(publisher.directory):
        with pytest.raises(sync.SyncError, match='another'):
            publisher.run()


def test_formula_parser_fails_closed():
    assert sync.formula_fields(FORMULA)['version'] == '0.11.9'
    with pytest.raises(sync.SyncError):
        sync.formula_fields(FORMULA.replace('archive/' + A, 'archive/main'))
    with pytest.raises(sync.SyncError):
        sync.formula_fields(FORMULA + '  version "0.12.0"\n')


def test_ci_requires_exact_successful_required_checks(monkeypatch):
    github = sync.GitHub('/fake/gh')
    runs = [{'name': 'CI', 'status': 'completed', 'conclusion': 'success',
             'app': {'slug': 'github-actions'}, 'head_sha': A}]
    statuses = {'state': 'pending', 'statuses': []}
    monkeypatch.setattr(github, 'api', lambda path: {'total_count': len(runs), 'check_runs': runs} if 'check-runs' in path else statuses)
    assert github.ci(sync.SOURCE, A)
    runs[0]['status'] = 'in_progress'
    assert not github.ci(sync.SOURCE, A)
    runs[0].update(status='completed', conclusion='failure')
    with pytest.raises(sync.SyncError, match='CI failed'):
        github.ci(sync.SOURCE, A)
    runs[0].update(conclusion='skipped')
    assert not github.ci(sync.SOURCE, A)


def test_ci_ignores_optional_checks_and_requires_all_tap_gates(monkeypatch):
    github = sync.GitHub('/fake/gh')
    def gate(name, **overrides):
        return {'name': name, 'status': 'completed', 'conclusion': 'success',
                'app': {'slug': 'github-actions'}, 'head_sha': A, **overrides}
    runs = [gate('CI'), gate('Scorecard analysis', conclusion='failure'),
            gate('Optional analysis', status='in_progress', conclusion=None)]
    monkeypatch.setattr(github, 'api', lambda path: {'total_count': len(runs), 'check_runs': runs})
    assert github.ci(sync.SOURCE, A)
    assert not github.ci(sync.TAP, A)
    runs.append(gate('formula (macos-latest)'))
    assert not github.ci(sync.TAP, A)
    runs.append(gate('formula (ubuntu-latest)'))
    assert github.ci(sync.TAP, A)
    runs[-1]['conclusion'] = 'failure'
    with pytest.raises(sync.SyncError, match='CI failed'):
        github.ci(sync.TAP, A)


def test_gh_structured_input_and_timeout(monkeypatch):
    seen = []
    def execute(argv, **kwargs):
        seen.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=b'{"ok": true}')
    monkeypatch.setattr(sync.subprocess, 'run', execute)
    assert sync.GitHub('/fake/gh').api('repos/a/b', 'POST', {'body': '$(secret)\n"'}) == {'ok': True}
    assert json.loads(seen[0][1]['input']) == {'body': '$(secret)\n"'}
    assert seen[0][1]['timeout'] == 45
    assert seen[0][0][-2:] == ['--input', '-']
    monkeypatch.setattr(sync.subprocess, 'run', lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired('gh', 45)))
    with pytest.raises(sync.SyncError, match='timed out'):
        sync.command(['/fake/gh'])


def test_launchagent_optin_stable_paths_and_private_files(sandbox, monkeypatch):
    home, _, _, publisher = sandbox
    stable = home / 'brew/opt/dotfiles/libexec/lib/macos/rectangle_sync.py'
    python = home / 'brew/opt/python@3.14/bin/python3.14'
    gh = home / 'brew/bin/gh'
    for path in (stable, python, gh):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    monkeypatch.setattr(sync, 'runtime_paths', lambda: (stable, python, gh))
    monkeypatch.setattr(sync, 'Publisher', lambda *args: publisher)
    executed = []
    monkeypatch.setattr(sync.subprocess, 'run', lambda argv, **kwargs: executed.append(argv) or SimpleNamespace(returncode=0, stdout=b''))
    assert 'baseline' in sync.install()
    path = sync.agent_path()
    config = plistlib.loads(path.read_bytes())
    assert config['StartInterval'] == 3600
    assert config['ProgramArguments'] == [str(python), str(stable), 'run']
    assert config['EnvironmentVariables']['HOME'] == str(home)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE((sync.home_dir() / 'source.json').stat().st_mode) == 0o600
    assert any('bootstrap' in command for command in executed)
    sync.uninstall()
    assert not path.exists()
    assert not (sync.home_dir() / 'source.json').exists()
    assert publisher.state_path.exists()


def test_real_api_retry_reuses_existing_pr_and_enables_auto_merge(monkeypatch):
    github = sync.GitHub('/fake/gh')
    calls = []
    def api(path, method='GET', body=None):
        calls.append((path, method, body))
        if 'matching-refs' in path:
            return [{'ref': 'refs/heads/automation/test', 'object': {'sha': A}}]
        if '/contents/' in path:
            import base64
            return {'content': base64.b64encode(b'expected').decode()}
        if '/pulls?' in path:
            return [{'number': 42, 'state': 'open'}]
        pytest.fail('unexpected API mutation: ' + path)
    monkeypatch.setattr(github, 'api', api)
    commands = []
    monkeypatch.setattr(sync, 'command', lambda argv, data=None: commands.append(argv))
    assert github.ensure_pr(sync.SOURCE, 'automation/test', A, sync.PROFILE, 'expected', 'title', 'body') == 42
    assert all(method == 'GET' for _, method, _ in calls)
    assert commands == [['/fake/gh', 'pr', 'merge', '42', '--repo', sync.SOURCE, '--auto', '--squash']]


def test_behind_pr_updates_expected_head_without_bypassing_checks(monkeypatch):
    github = sync.GitHub('/fake/gh')
    calls = []
    def api(path, method='GET', body=None):
        calls.append((path, method, body))
        if method == 'PUT':
            return {}
        return {'merged': False, 'state': 'open', 'mergeable': True,
                'mergeable_state': 'behind', 'head': {'sha': A}}
    monkeypatch.setattr(github, 'api', api)
    monkeypatch.setattr(github, 'ci', lambda repo, sha: False)
    assert github.merged(sync.TAP, 42) is None
    assert calls[-1] == (f'repos/{sync.TAP}/pulls/42/update-branch', 'PUT', {'expected_head_sha': A})


def test_ci_rejects_untrusted_app_and_wrong_commit(monkeypatch):
    github = sync.GitHub('/fake/gh')
    run = {'name': 'CI', 'status': 'completed', 'conclusion': 'success',
           'app': {'slug': 'untrusted'}, 'head_sha': A}
    monkeypatch.setattr(github, 'api', lambda path: {'total_count': 1, 'check_runs': [run]} if 'check-runs' in path else {'state': 'pending', 'statuses': []})
    assert not github.ci(sync.SOURCE, A)
    run['app']['slug'] = 'github-actions'
    run['head_sha'] = B
    assert not github.ci(sync.SOURCE, A)


def test_other_release_wins_pending_tap_waits_for_both_ci_then_retires(sandbox, monkeypatch):
    _, current, github, publisher = sandbox
    publisher.run()
    current['leftHalf'] = {'keyCode': 12}
    publisher.run()
    # Source is merged; create a tap PR which remains pending.
    github.source_head = B
    monkeypatch.setattr(github, 'merged', lambda repo, number: B if repo == sync.SOURCE else None)
    assert 'tap PR pending' in publisher.run()
    branch = state(publisher)['operation']['tap']['branch']
    github.formula = FORMULA.replace(A, B)
    github.tap_head = C
    checked = set()
    monkeypatch.setattr(github, 'ci', lambda repo, sha: (repo, sha) in checked)
    assert 'CI pending' in publisher.run()
    assert not github.closed
    checked.add((sync.SOURCE, B))
    assert 'CI pending' in publisher.run()
    checked.add((sync.TAP, C))
    assert 'released' in publisher.run()
    assert github.closed == [(sync.TAP, branch)]
    assert len(github.calls) == 2


def test_stale_existing_tap_pr_is_closed_before_replacement(sandbox, monkeypatch):
    _, current, github, publisher = sandbox
    publisher.run()
    current['leftHalf'] = {'keyCode': 12}
    publisher.run()
    github.source_head = B
    monkeypatch.setattr(github, 'merged', lambda repo, number: B if repo == sync.SOURCE else None)
    publisher.run()
    old = state(publisher)['operation']['tap']['branch']
    github.tap_head = C
    github.formula = FORMULA.replace('0.11.9', '0.11.10')
    assert 'recalculate' in publisher.run()
    assert github.closed == [(sync.TAP, old)]
    assert 'tap' not in state(publisher)['operation']
    assert 'tap PR pending' in publisher.run()
    new = state(publisher)['operation']['tap']['branch']
    assert old != new
    assert '  version "0.11.11"' in state(publisher)['operation']['tap']['formula']
