"""Load the extensionless commands in ``tools/`` as modules, for testing."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import pathlib
import sys
import types

TOOLS = pathlib.Path(__file__).resolve().parent.parent / "tools"


def load(name: str) -> types.ModuleType:
    """Import ``tools/<name>`` under ``name`` with hyphens as underscores."""
    path = TOOLS / name
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    # Registered before execution, as an import would.
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module
