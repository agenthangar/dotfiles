"""Render plan Markdown using the bundled, privately loaded parser."""

import importlib.util
from pathlib import Path
import sys


def render(source):
    name = "_t_mistune"
    if name not in sys.modules:
        path = Path(__file__).resolve().parent / "vendor" / "mistune" / "__init__.py"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    markdown = sys.modules[name].create_markdown(
        escape=True, plugins=["table", "task_lists", "strikethrough", "footnotes", "url"])
    return markdown(source)
