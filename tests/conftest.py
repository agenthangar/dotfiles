"""Never write live Mac preferences from sandboxed integration tests."""
import pytest


@pytest.fixture(autouse=True)
def isolate_macos_preferences(monkeypatch):
    monkeypatch.setenv("DOTFILES_NO_MACOS", "1")
