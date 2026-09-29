"""Shared renderer navigation and review gates, without changing terminal or disk state."""
import io

import pytest


def make_ui(t, keys, width=80, height=24):
    ui = t._RailUI.__new__(t._RailUI)
    ui.st, ui.out = t.Style(tty=True), io.StringIO()
    ui.rail = f'  {ui.st.y}│{ui.st.r}'
    ui.painted = [0]
    ui.cols, ui.lines = lambda: width, lambda: height
    events = iter(keys)
    ui.read_key = lambda: next(events)
    ui.raw = lambda: None
    ui.restored = False
    ui.restore = lambda: setattr(ui, 'restored', True)
    ui.frames = []
    ui.repaint = lambda lines: ui.frames.append(lines)
    return ui


@pytest.mark.parametrize('width,height', [(80, 24), (44, 20), (32, 12), (20, 8)])
@pytest.mark.parametrize('multi', [False, True])
def test_shared_picker_keeps_focus_and_primary_action_visible(t_mod, width, height, multi):
    ui = make_ui(t_mod, ['j'] * 11 + ['?'] + ['k'] * 11 + ['s'], width, height)
    rows = [(str(i), f'Setting {i}') for i in range(12)]
    assert ui.pick('Settings', rows, multi=multi,
        values={str(i): 'a-long-model-or-host-name' for i in range(10)},
        sections={'0': 'Defaults', '5': 'Hosts', '9': 'Finish'},
        primary=('s', 'SAVE', ''), shortcuts={'s': 'save'}) == 'save'
    for frame in ui.frames:
        assert sum(t_mod._term_rows(line, width) for line in frame) <= height
        assert any('▸' in line for line in frame)
        assert any('SAVE' in line for line in frame)
        assert all(t_mod._vis_len(line) <= width for line in frame)


def test_setup_and_install_share_rows_and_review_without_running_actions(t_mod, monkeypatch):
    items = t_mod._setup_items([('/code/api', 'api')], {'existing': '/code/existing'}, [], {}, None, [], '~/code')
    ui = make_ui(t_mod, ['space', 'enter', 'y'])
    monkeypatch.setattr(t_mod, '_RailUI', lambda: ui)
    assert t_mod._setup_wizard(items, set())
    assert t_mod._setup_result(items)[0] == {'api': '/code/api'}
    assert ui.restored
    assert any('REVIEW CHANGES' in line for frame in ui.frames for line in frame)
    assert any('APPLY CHANGES' in line for frame in ui.frames for line in frame)
    # Reuse the same real renderer with a fresh key stream for install.
    events = iter(['space', 'enter', 'n', 'q'])
    ui.read_key = lambda: next(events)
    items = [{'t': 'header', 'label': 'Agent CLIs'},
             {'t': 'locked', 'alias': 'existing', 'value': 'installed'},
             {'t': 'toggle', 'alias': 'codex', 'value': 'install', 'checked': False}]
    assert not t_mod._install_wizard(items, lambda: [{'step': 'install', 'do': 'run', 'label': 'Install codex'}])
    assert items[-1]['checked'] and ui.restored


def test_review_can_scroll_and_back_out_with_action_still_visible(t_mod):
    ui = make_ui(t_mod, ['j'] * 10 + ['n'], 44, 20)
    assert ui.page('Review', [f'      Change {i}' for i in range(40)], ('y', 'n'),
                   keys='n back', primary=('y', 'APPLY', '')) == 'n'
    for frame in ui.frames:
        assert any('APPLY' in line for line in frame)
        assert sum(t_mod._term_rows(line, 44) for line in frame) <= 20
