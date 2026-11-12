#!/usr/bin/env python3
"""Apply the explicit portable profile, merging only managed preference keys."""
import datetime
import json
import math
import os
from pathlib import Path
import platform
import plistlib
import subprocess
import sys
import tempfile


class PreferenceError(Exception):
    pass


def load_profile(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise PreferenceError(f'duplicate profile key: {key}')
            result[key] = value
        return result

    data = json.loads(path.read_text(), object_pairs_hook=unique)
    if set(data) != {'version', 'domains'} or type(data['version']) is not int or data['version'] != 1:
        raise PreferenceError('unsupported preference profile')
    if not isinstance(data['domains'], dict) or not data['domains']:
        raise PreferenceError('profile domains must be a nonempty dictionary')
    types = {'bool': bool, 'int': int, 'float': float, 'string': str, 'dict': dict}
    domains = {}
    for domain, settings in data['domains'].items():
        if not domain or domain.startswith('-') or not isinstance(settings, dict) or not settings:
            raise PreferenceError(f'invalid preference domain: {domain}')
        domains[domain] = {}
        for key, entry in settings.items():
            if not key or key.startswith('-') or not isinstance(entry, dict) or set(entry) != {'type', 'value'}:
                raise PreferenceError(f'invalid preference entry: {domain} {key}')
            kind, value = entry['type'], entry['value']
            if not isinstance(kind, str) or kind not in types or type(value) is not types[kind]:
                raise PreferenceError(f'invalid preference type: {domain} {key}')
            if kind == 'dict' and any(not isinstance(k, str) or type(v) is not int for k, v in value.items()):
                raise PreferenceError(f'invalid shortcut dictionary: {domain} {key}')
            if kind == 'float' and not math.isfinite(value):
                raise PreferenceError(f'invalid preference number: {domain} {key}')
            # Validate serialization of the entire manifest before reading or writing.
            plistlib.dumps(value)
            domains[domain][key] = value
    return domains


def defaults(*args):
    return subprocess.run(['defaults', *args], capture_output=True)


def read_domain(domain):
    result = defaults('export', domain, '-')
    if result.returncode:
        error = result.stderr.decode(errors='replace').strip()
        if 'does not exist' in error or 'not found' in error:
            return {}
        raise PreferenceError(f'could not read {domain}: {error}')
    try:
        values = plistlib.loads(result.stdout)
    except Exception as exc:
        raise PreferenceError(f'invalid exported preferences for {domain}: {exc}') from exc
    if not isinstance(values, dict):
        raise PreferenceError(f'invalid exported preferences for {domain}')
    return values


def same(a, b):
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[key], b[key]) for key in a)
    return a == b


def apply(profile):
    domains = load_profile(profile)
    changes, before = [], {}
    for domain, settings in domains.items():
        current = read_domain(domain)
        for key, value in settings.items():
            if key not in current or not same(current[key], value):
                changes.append((domain, key, value))
                before.setdefault(domain, {})[key] = {'present': key in current}
                if key in current:
                    before[domain][key]['value'] = current[key]
    if not changes:
        print('✓ Managed macOS preferences already match (no changes)')
        return
    # Back up only managed values, including explicit absence, before any writes.
    base = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'dotfiles/backups'
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ-')
    backup = Path(tempfile.mkdtemp(prefix=stamp, dir=base))
    saved = backup / 'managed-values.plist'
    fd = os.open(saved, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as output:
        plistlib.dump(before, output)
    print(f'Saved backup: {backup}', flush=True)
    for domain, key, value in changes:
        # XML preserves booleans, real numbers and integer shortcut dictionaries.
        # Per-key writes leave every unrelated preference untouched.
        xml = plistlib.dumps(value).decode()
        result = defaults('write', domain, key, xml)
        if result.returncode:
            error = result.stderr.decode(errors='replace').strip()
            raise PreferenceError(f'could not write {domain} {key}: {error}; backup: {backup}')
    print(f'✓ Managed macOS preferences backed up and applied ({len(changes)} keys)')
    if any(domain == 'com.knollsoft.Rectangle' for domain, _, _ in changes):
        print('Relaunch Rectangle if running. On a new Mac, open it and grant Accessibility access.')
    print('Some desktop preferences may require logout/login. Apps were not restarted.')


def main():
    if len(sys.argv) != 1:
        print('Usage: apply.py (apply the bundled macOS preference profile)', file=sys.stderr)
        return 2
    if os.environ.get('DOTFILES_NO_MACOS') == '1':
        print('macOS preferences: skipped (DOTFILES_NO_MACOS=1)')
        return 0
    if platform.system() != 'Darwin':
        print(f'macOS preferences: skipped ({platform.system()})')
        return 0
    try:
        apply(Path(__file__).with_name('preferences.json'))
    except (PreferenceError, OSError, ValueError, TypeError, OverflowError) as exc:
        print(f'macOS preferences: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
