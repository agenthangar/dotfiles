"""Pytest fixtures that import the extensionless bin scripts as modules.

`bin/t` and `bin/pr-watch` are executables symlinked onto PATH by install.sh and
referenced by the `t()` zsh shim and the pr-watch launchd plist — renaming them to
`.py` would break those references. So we load them in place via importlib instead.

Both scripts guard their entrypoint with `if __name__ == "__main__"`, so importing
runs only their (side-effect-free) module-level constant setup, not the CLI.
"""

import importlib.util
import pathlib
import sys
from importlib.machinery import SourceFileLoader

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
BIN = REPO_ROOT / "bin"


def load_script(path, mod_name):
    """Import an extensionless script at `path` as a module named `mod_name`.

    spec_from_file_location returns None for a path with no recognised source
    suffix, so we hand it an explicit SourceFileLoader.
    """
    loader = SourceFileLoader(mod_name, str(path))
    spec = importlib.util.spec_from_file_location(mod_name, str(path), loader=loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def t_mod():
    return load_script(BIN / "t", "t_bin")


@pytest.fixture(scope="session")
def prwatch_mod():
    return load_script(BIN / "pr-watch", "pr_watch_bin")
