"""Load the safety service from its legacy hyphenated directory exactly once."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from fastapi import FastAPI


def _load_legacy_service() -> ModuleType:
    module_name = "marga_safety_detectors_runtime"
    loaded = sys.modules.get(module_name)
    if isinstance(loaded, ModuleType):
        return loaded
    source = Path(__file__).resolve().parents[1] / "safety-detectors" / "main.py"
    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load safety service from {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_service = _load_legacy_service()
app = _service.app
assert isinstance(app, FastAPI)


def initialize() -> None:
    """Initialize detector state when mounted under another ASGI application."""
    _service._init_all()
