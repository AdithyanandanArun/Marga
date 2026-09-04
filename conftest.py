"""Root conftest: register the hyphenated services/safety-detectors directory
as the importable ``services.safety_detectors`` package so that all test
modules can use normal dotted imports, and add repo root to sys.path.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types

_BASE = os.path.dirname(__file__)
_HYPHENATED = os.path.join(_BASE, "services", "safety-detectors")
_DETECTORS = os.path.join(_HYPHENATED, "detectors")

# Add repo root to path so `services.simulation_adapter` is importable
sys.path.insert(0, _BASE)

def _register(module_name: str, init_path: str, search_locations: list[str]) -> None:
    """Load a module from *init_path* and register it in sys.modules."""
    if module_name in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        module_name,
        init_path,
        submodule_search_locations=search_locations,
    )
    if spec is None or spec.loader is None:
        return
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)


def _register_submodule(parent: str, child: str, filepath: str) -> None:
    """Load a single .py submodule."""
    full_name = f"{parent}.{child}"
    if full_name in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(full_name, filepath)
    if spec is None or spec.loader is None:
        return
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = mod
    spec.loader.exec_module(mod)


# -- Register packages -------------------------------------------------------
# Ensure the ``services`` parent package is in sys.modules.
_services_init = os.path.join(_BASE, "services", "__init__.py")
if os.path.isfile(_services_init):
    _register(
        "services",
        _services_init,
        [os.path.join(_BASE, "services")],
    )

_register(
    "services.safety_detectors",
    os.path.join(_HYPHENATED, "__init__.py"),
    [_HYPHENATED],
)
_register(
    "services.safety_detectors.detectors",
    os.path.join(_DETECTORS, "__init__.py"),
    [_DETECTORS],
)

# -- Register every detector submodule automatically -------------------------
if os.path.isdir(_DETECTORS):
    for fname in sorted(os.listdir(_DETECTORS)):
        if fname.endswith(".py") and fname != "__init__.py":
            mod_name = fname[:-3]
            _register_submodule(
                "services.safety_detectors.detectors",
                mod_name,
                os.path.join(_DETECTORS, fname),
            )
