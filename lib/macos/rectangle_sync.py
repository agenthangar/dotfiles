#!/usr/bin/env python3
"""Private, opt-in source-Mac Rectangle publisher. Not part of the dots CLI."""
import base64
import contextlib
import copy
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

_spec = importlib.util.spec_from_file_location('dotfiles_preferences', Path(__file__).with_name('apply.py'))
prefs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prefs)

DOMAIN = 'com.knollsoft.Rectangle'
SOURCE = 'agenthangar/dotfiles'
TAP = 'agenthangar/homebrew-tap'
PROFILE = 'lib/macos/preferences.json'
FORMULA = 'Formula/dotfiles.rb'
LABEL = 'com.agenthangar.dotfiles.rectangle-sync'
MISSING = {'present': False}


class SyncError(Exception):
    pass


def home_dir():
    return Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'dotfiles/rectangle-sync'


def private_dir(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise SyncError('private state directory must not be a symlink')
    path.chmod(0o700)


def atomic(path, data):
    private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save(path, value):
    atomic(path, (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n').encode())


@contextlib.contextmanager
def locked(directory):
    private_dir(directory)
    fd = os.open(directory / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SyncError('another Rectangle sync is running') from exc
        yield


def load_profile(text):
    # Reuse the applier's complete type and serialization validation.
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'profile.json'
        path.write_text(text)
        prefs.load_profile(path)
    return json.loads(text)


def managed(profile):
    return {key: entry for key, entry in profile['domains'][DOMAIN].items()
            if key != 'wasWelcomeDisplayed'}


def capture(schema):
    values = prefs.read_domain(DOMAIN)
    result = {}
    for key, entry in schema.items():
        if key not in values:
            if entry['type'] != 'dict':
                raise SyncError(f'missing managed Rectangle preference: {key}')
            result[key] = dict(MISSING)
            continue
        value = values[key]
        if type(value) is not type(entry['value']):
            raise SyncError(f'invalid managed Rectangle preference type: {key}')
        if isinstance(value, dict) and any(not isinstance(k, str) or type(v) is not int for k, v in value.items()):
            raise SyncError(f'invalid Rectangle shortcut: {key}')
        if isinstance(value, float) and not prefs.math.isfinite(value):
            raise SyncError(f'invalid Rectangle number: {key}')
        result[key] = {'present': True, 'value': value}
    return result


def stable_capture(schema):
    first = capture(schema)
    time.sleep(2)
    second = capture(schema)
    if not prefs.same(first, second):
        raise SyncError('Rectangle preferences changed during capture; retry next hour')
    return second


def upstream_values(schema):
    return {key: entry['value'] for key, entry in schema.items()}


def merge_changes(baseline, local, remote, schema):
    if set(baseline['local']) != set(schema) or set(baseline['upstream']) != set(schema):
        raise SyncError('managed Rectangle keys changed; inspect and archive private state before rebaselining')
    changes = {}
    for key, observed in local.items():
        if prefs.same(observed, baseline['local'][key]):
            continue
        desired = observed['value'] if observed['present'] else {}
        if prefs.same(desired, remote[key]):
            continue
        if not prefs.same(remote[key], baseline['upstream'][key]):
            raise SyncError(f'upstream/local Rectangle conflict: {key}')
        changes[key] = desired
    return changes


def command(argv, data=None):
    try:
        result = subprocess.run(argv, input=data, capture_output=True, timeout=45)
    except subprocess.TimeoutExpired as exc:
        raise SyncError('command timed out; operation will resume next hour') from exc
    if result.returncode:
        raise SyncError(f'{Path(argv[0]).name} command failed ({result.returncode}); check authentication and PR checks')
    return result.stdout


class GitHub:
    def __init__(self, executable):
        self.executable = str(executable)

    def api(self, path, method='GET', body=None):
        argv = [self.executable, 'api', path, '--method', method]
        data = None
        if body is not None:
            argv += ['--input', '-']
            data = json.dumps(body).encode()
        return json.loads(command(argv, data))

    def head(self, repo):
        return self.api(f'repos/{repo}/git/ref/heads/main')['object']['sha']

    def file(self, repo, path, ref):
        item = self.api(f'repos/{repo}/contents/{path}?ref={ref}')
        return base64.b64decode(item['content']).decode()

    def branch_head(self, repo, branch):
        # A successful list distinguishes absence from network/authentication errors.
        refs = self.api(f'repos/{repo}/git/matching-refs/heads/{branch}')
        exact = [ref for ref in refs if ref['ref'] == 'refs/heads/' + branch]
        return exact[0]['object']['sha'] if exact else None

    def ensure_pr(self, repo, branch, base, path, content, title, body):
        head = self.branch_head(repo, branch)
        if head is None:
            original = self.api(f'repos/{repo}/git/commits/{base}')
            blob = self.api(f'repos/{repo}/git/blobs', 'POST', {
                'content': content, 'encoding': 'utf-8'})
            tree = self.api(f'repos/{repo}/git/trees', 'POST', {
                'base_tree': original['tree']['sha'], 'tree': [
                    {'path': path, 'mode': '100644', 'type': 'blob', 'sha': blob['sha']}]})
            commit = self.api(f'repos/{repo}/git/commits', 'POST', {
                'message': title, 'tree': tree['sha'], 'parents': [base]})
            self.api(f'repos/{repo}/git/refs', 'POST', {
                'ref': 'refs/heads/' + branch, 'sha': commit['sha']})
        elif self.file(repo, path, head) != content:
            raise SyncError('operation branch changed; inspect it before retrying')
        prs = self.api(f'repos/{repo}/pulls?head=agenthangar:{branch}&base=main&state=all')
        if len(prs) > 1:
            raise SyncError('multiple PRs found for one operation')
        if prs:
            pr = prs[0]
            if pr['state'] == 'closed' and not pr.get('merged_at'):
                raise SyncError('operation PR was closed without merging; inspect before retrying')
        else:
            pr = self.api(f'repos/{repo}/pulls', 'POST', {
                'head': branch, 'base': 'main', 'title': title, 'body': body})
        if pr['state'] == 'open':
            command([self.executable, 'pr', 'merge', str(pr['number']), '--repo', repo, '--auto', '--squash'])
        return pr['number']

    def close_pending(self, repo, branch):
        prs = self.api(f'repos/{repo}/pulls?head=agenthangar:{branch}&base=main&state=open')
        if len(prs) > 1:
            raise SyncError('multiple PRs found for one operation')
        for pr in prs:
            self.api(f'repos/{repo}/pulls/{pr["number"]}', 'PATCH', {'state': 'closed'})

    def merged(self, repo, number):
        pr = self.api(f'repos/{repo}/pulls/{number}')
        if not pr.get('merged'):
            if pr['state'] == 'closed' or pr.get('mergeable') is False:
                raise SyncError('PR closed or conflicted; inspect before retrying')
            head = pr['head']['sha']
            self.ci(repo, head)  # Failures stop, pending remains resumable.
            if pr.get('mergeable_state') == 'behind':
                self.api(f'repos/{repo}/pulls/{number}/update-branch', 'PUT', {'expected_head_sha': head})
            return None
        sha = pr['merge_commit_sha']
        return sha if self.ci(repo, sha) else None

    def ci(self, repo, sha):
        checks = self.api(f'repos/{repo}/commits/{sha}/check-runs?per_page=100')
        statuses = self.api(f'repos/{repo}/commits/{sha}/status')
        runs = checks['check_runs']
        if checks['total_count'] > len(runs):
            raise SyncError('too many CI checks to verify safely')
        if any(run['status'] == 'completed' and run['conclusion'] not in ('success', 'neutral', 'skipped') for run in runs):
            raise SyncError('CI failed; inspect the operation PR')
        if statuses['state'] in ('failure', 'error'):
            raise SyncError('commit status failed; inspect the operation PR')
        required = ('CI',) if repo == SOURCE else ('formula (macos-latest)', 'formula (ubuntu-latest)')
        # A merged commit must have its own required CI, not merely unrelated checks.
        required_passed = all(any(
            run['name'] == name and run['status'] == 'completed' and run['conclusion'] == 'success'
            and run.get('app', {}).get('slug') == 'github-actions'
            and run.get('head_sha') == sha for run in runs) for name in required)
        return required_passed and all(run['status'] == 'completed' for run in runs) and (
            not statuses['statuses'] or statuses['state'] == 'success')

    def contains(self, ancestor, descendant):
        comparison = self.api(f'repos/{SOURCE}/compare/{ancestor}...{descendant}')
        return comparison['status'] in ('identical', 'ahead')


def archive_sha(sha):
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise SyncError('invalid source commit')
    digest = hashlib.sha256()
    with urllib.request.urlopen(f'https://github.com/{SOURCE}/archive/{sha}.tar.gz', timeout=45) as response:
        while chunk := response.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def formula_fields(text):
    patterns = {
        'sha': r'^  url "https://github\.com/agenthangar/dotfiles/archive/([0-9a-f]{40})\.tar\.gz"$',
        'version': r'^  version "(\d+\.\d+\.\d+)"$',
        'hash': r'^  sha256 "([0-9a-f]{64})"$',
    }
    result = {}
    for key, pattern in patterns.items():
        matches = list(re.finditer(pattern, text, re.M))
        if len(matches) != 1:
            raise SyncError(f'cannot safely parse formula {key}')
        result[key] = matches[0].group(1)
    return result


def update_formula(text, sha, digest):
    old = formula_fields(text)
    major, minor, patch = map(int, old['version'].split('.'))
    version = f'{major}.{minor}.{patch + 1}'
    return text.replace(f'/archive/{old["sha"]}.tar.gz"', f'/archive/{sha}.tar.gz"', 1).replace(
        f'  version "{old["version"]}"', f'  version "{version}"', 1).replace(
        f'  sha256 "{old["hash"]}"', f'  sha256 "{digest}"', 1)


class Publisher:
    def __init__(self, github, directory=None, profile=None):
        self.github = github
        self.directory = directory or home_dir()
        self.state_path = self.directory / 'state.json'
        self.schema = managed(load_profile((profile or Path(__file__).with_name('preferences.json')).read_text()))

    def remote(self):
        sha = self.github.head(SOURCE)
        profile = load_profile(self.github.file(SOURCE, PROFILE, sha))
        schema = managed(profile)
        if set(schema) != set(self.schema) or any(schema[k]['type'] != self.schema[k]['type'] for k in schema):
            raise SyncError('upstream managed schema changed; upgrade dots before retrying')
        return sha, profile, upstream_values(schema)

    def run(self):
        with locked(self.directory):
            state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {'version': 1}
            if state.get('version') != 1:
                raise SyncError('unsupported sync state')
            if state.get('operation'):
                return self.resume(state)
            sha, profile, remote = self.remote()
            local = stable_capture(self.schema)
            if 'baseline' not in state:
                state['baseline'] = {'local': local, 'upstream': remote}
                save(self.state_path, state)
                return 'baseline recorded; historical differences were not published'
            changes = merge_changes(state['baseline'], local, remote, self.schema)
            if not changes:
                baseline = {'local': local, 'upstream': remote}
                if state['baseline'] != baseline:
                    state['baseline'] = baseline
                    save(self.state_path, state)
                return 'no Rectangle changes'
            updated = copy.deepcopy(profile)
            for key, value in changes.items():
                updated['domains'][DOMAIN][key]['value'] = value
            text = json.dumps(updated, indent=2, ensure_ascii=False, allow_nan=False) + '\n'
            identity = hashlib.sha256(json.dumps([sha, local, changes], sort_keys=True).encode()).hexdigest()[:20]
            state['operation'] = {'id': identity, 'base': sha, 'profile': text, 'local': local}
            save(self.state_path, state)  # Durable intent before any GitHub mutation.
            return self.resume(state)

    def resume(self, state):
        op = state['operation']
        github = self.github
        if 'source_pr' not in op:
            op['source_pr'] = github.ensure_pr(
                SOURCE, 'automation/rectangle-' + op['id'], op['base'], PROFILE, op['profile'],
                'Sync managed Rectangle preferences',
                'Publish changes from the explicitly opted-in source Mac. Only existing managed Rectangle keys are captured.\n\nRefs #255.')
            save(self.state_path, state)
        merged = github.merged(SOURCE, op['source_pr'])
        if not merged:
            return 'source PR pending; will resume next hour'
        if op.get('merged') != merged:
            op['merged'] = merged
            save(self.state_path, state)
        # Another release may win at any point, including while our PR is pending.
        head = github.head(TAP)
        formula = github.file(TAP, FORMULA, head)
        source = formula_fields(formula)['sha']
        if github.contains(merged, source):
            if not github.ci(SOURCE, source) or not github.ci(TAP, head):
                return 'existing release CI pending; will resume next hour'
            if op.get('tap'):
                github.close_pending(TAP, op['tap']['branch'])
            return self.finish(state)
        if not github.contains(source, merged):
            raise SyncError('tap source and merged preference commit diverged; refusing rollback')
        if op.get('tap') and head != op['tap']['base']:
            # Close before replacing: never force a stale formula over another release.
            github.close_pending(TAP, op['tap']['branch'])
            del op['tap']
            save(self.state_path, state)
            return 'tap advanced; will recalculate release next hour'
        if 'tap' not in op:
            # Release current main so unrelated commits merged meanwhile are preserved.
            release = github.head(SOURCE)
            if not github.contains(merged, release):
                raise SyncError('source main no longer contains captured preferences')
            if not github.ci(SOURCE, release):
                return 'source main CI pending; will resume next hour'
            op['tap'] = {
                'base': head, 'formula': update_formula(formula, release, archive_sha(release)),
                'branch': 'automation/rectangle-' + op['id'] + '-' + head[:12],
            }
            save(self.state_path, state)
        tap = op['tap']
        if 'pr' not in tap:
            # A main update during download/state persistence is handled before mutation.
            if github.head(TAP) != tap['base']:
                github.close_pending(TAP, tap['branch'])
                del op['tap']
                save(self.state_path, state)
                return 'tap advanced; will recalculate release next hour'
            tap['pr'] = github.ensure_pr(
                TAP, tap['branch'], tap['base'], FORMULA, tap['formula'],
                'Release Rectangle preference sync',
                'Patch release of verified source containing managed Rectangle changes.\n\nRefs #67; source agenthangar/dotfiles#255.')
            save(self.state_path, state)
        if not github.merged(TAP, tap['pr']):
            return 'tap PR pending; will resume next hour'
        # Confirm current tap contains our captured commit, including concurrent newer releases.
        current_head = github.head(TAP)
        current = github.file(TAP, FORMULA, current_head)
        current_source = formula_fields(current)['sha']
        if not github.contains(merged, current_source):
            raise SyncError('merged tap does not contain captured preferences')
        if not github.ci(SOURCE, current_source) or not github.ci(TAP, current_head):
            return 'merged release CI pending; will resume next hour'
        return self.finish(state)

    def finish(self, state):
        op = state['operation']
        profile = load_profile(self.github.file(SOURCE, PROFILE, op['merged']))
        state['baseline'] = {'local': op['local'], 'upstream': upstream_values(managed(profile))}
        del state['operation']
        save(self.state_path, state)
        return 'Rectangle changes released; baseline advanced'


def runtime_paths():
    root = Path(__file__).parent.parent.parent
    marker = root / '.dotfiles-package'
    if not marker.is_file():
        raise SyncError('install the released Homebrew package before enabling the source Mac')
    stable = Path(marker.read_text().strip())
    if not stable.is_absolute() or stable.name != 'libexec' or stable.parent.name != 'dotfiles' or stable.parent.parent.name != 'opt':
        raise SyncError('invalid stable package path')
    prefix = stable.parent.parent.parent
    return stable / 'lib/macos/rectangle_sync.py', prefix / 'opt/python@3.14/bin/python3.14', prefix / 'bin/gh'


def agent_path():
    return Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')


def install():
    script, python, gh = runtime_paths()
    if not all(path.is_file() for path in (script, python, gh)):
        raise SyncError('required package, Python or gh executable is missing')
    directory = home_dir()
    # Protect local Rectangle values before activation; leave protection on if setup fails.
    save(directory / 'source.json', {'version': 1, 'enabled': True})
    publisher = Publisher(GitHub(gh))
    result = publisher.run()
    configuration = {
        'Label': LABEL, 'ProgramArguments': [str(python), str(script), 'run'],
        'StartInterval': 3600, 'RunAtLoad': True,
        'EnvironmentVariables': {'HOME': str(Path.home()), 'XDG_CONFIG_HOME': str(directory.parent.parent)},
        'StandardOutPath': '/dev/null', 'StandardErrorPath': '/dev/null',
        'ProcessType': 'Background',
    }
    path = agent_path()
    # LaunchAgents is shared with other jobs: do not chmod its directory.
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + LABEL, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            plistlib.dump(configuration, out)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    target = f'gui/{os.getuid()}'
    # bootout is idempotent; bootstrap failures are reported and protection retained.
    subprocess.run(['/bin/launchctl', 'bootout', target + '/' + LABEL], capture_output=True, timeout=15)
    command(['/bin/launchctl', 'bootstrap', target, str(path)])
    return result


def uninstall():
    subprocess.run(['/bin/launchctl', 'bootout', f'gui/{os.getuid()}/{LABEL}'], capture_output=True, timeout=15)
    agent_path().unlink(missing_ok=True)
    # Keep the baseline/pending operation for recovery. Explicit disable restores normal applies.
    (home_dir() / 'source.json').unlink(missing_ok=True)
    return 'hourly publishing disabled; normal Rectangle profile application restored'


def log(message):
    directory = home_dir()
    private_dir(directory)
    path = directory / 'sync.log'
    if path.exists() and path.stat().st_size > 1024 * 1024:
        os.replace(path, directory / 'sync.log.1')
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as out:
        out.write(datetime.datetime.now(datetime.timezone.utc).isoformat() + ' ' + message + '\n')


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ('install', 'uninstall', 'run', 'status'):
        print('Private Rectangle source helper: install | uninstall | run | status', file=sys.stderr)
        return 2
    try:
        if platform.system() != 'Darwin':
            raise SyncError('Rectangle source publishing requires macOS')
        action = sys.argv[1]
        if action == 'status':
            state = json.loads((home_dir() / 'state.json').read_text()) if (home_dir() / 'state.json').exists() else {}
            print('Enabled' if (home_dir() / 'source.json').exists() else 'Disabled')
            if state.get('operation'):
                print('Pending operation: ' + state['operation']['id'])
            print('Private state/logs: ' + str(home_dir()))
            return 0
        if action == 'install':
            message = install()
        elif action == 'uninstall':
            message = uninstall()
        else:
            marker = home_dir() / 'source.json'
            if not marker.exists() or json.loads(marker.read_text()) != {'version': 1, 'enabled': True}:
                raise SyncError('source Mac is not enabled; run the private install helper')
            _, _, gh = runtime_paths()
            message = Publisher(GitHub(gh)).run()
        log(message)
        print(message)
        return 0
    except (SyncError, prefs.PreferenceError, OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as exc:
        message = str(exc) if isinstance(exc, (SyncError, prefs.PreferenceError)) else type(exc).__name__ + '; inspect private state and retry'
        log('ERROR: ' + message)
        print('Rectangle sync: ' + message, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
