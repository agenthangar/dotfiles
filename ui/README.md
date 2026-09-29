# Shared terminal design

The dotfiles TUIs share a template so changes to spacing, color, focus, and
scrolling reach every menu. Keep command-specific decisions out of the renderer.

| Surface | Shared implementation | Consumers |
| --- | --- | --- |
| Inline wizards | `Style` and `_RailUI` in `bin/t` | `t config`, `t setup`, `t install`, `t new` |
| Searchable lists | `_t_fzf` in `ui/fzf.sh` | Session, worktree, paste, search, beam, and Cursor chat pickers |

## Visual rules

- Two-space outer margin; indent settings beneath cyan section headings.
- Cyan for current values, green for primary/add actions, amber for warnings/removal.
- A pointer and reverse-video row identify keyboard focus. Color is never the only cue.
- Align labels and values on wide screens; stack values beneath labels below 60 columns.
- Keep primary actions and keyboard hints in a footer outside the scrollable list.
- Clip plain text before adding color; count physical terminal rows when repainting.
- Show a review before applying changes. In config, quitting with pending edits offers
  Keep editing (the default), Review and save, and Discard changes and exit.

## Inline menu template

Use the existing `_RailUI` instance in `bin/t`; do not copy the rendering loop.

```python
ui.intro("t command", "A short description of what will change.")
ui.raw()
try:
    choice = ui.pick(
        "Settings",
        [("tool", "Default tool"), ("save", "Save changes")],
        values={"tool": "claude"},
        sections={"tool": "Session defaults"},
        accents={"save": "g"},
        primary=("s", "SAVE CHANGES", "2 pending"),
        shortcuts={"s": "save"},
    )
finally:
    ui.restore()
```

`intro`, `section`, `row`, `chrome`, and `frame` define the shared design.
`pick` combines them for ordinary menus. Editable lists such as setup/install use
`row` and `frame` with their own key handling: pass `(line, item_key)` pairs,
using `None` for nonselectable headings. `page` uses the same title/footer for
scrollable reviews; pass `primary=("y", "APPLY CHANGES", "")` and accept `y`
explicitly. Render notices with `done` and editable prompts with `cooked_input`.
Restore terminal state in `finally`. Keep non-TTY command fallbacks usable.

## fzf template

Source `ui/fzf.sh` and call `_t_fzf` instead of invoking `fzf` directly. `.zshrc`
loads it once per shell load; `cursor-beam` resolves it beside its real script.
The shared defaults provide a cyan/green palette, a strong selection highlight,
matching pointer/marker, outer margins, and a border. Existing caller-specific
arguments come last, retaining their filtering, multi-select, previews and height.
User `FZF_DEFAULT_OPTS` remains intact; this theme is scoped to dotfiles pickers.

## Checking a design change

Exercise config, setup, install, new-repo and review screens without applying
changes. Check 80×24 and 44×20 terminals, long labels/values, scrolling, multi-select,
and a small 32×12 viewport. Every selection and the primary action must stay
visible. Config cancellation tests must cover keeping, saving and explicit discard.
