"""Importable production entry point for Hrishi's safety-detectors service.

The on-disk service directory retains its historical ``safety-detectors``
name, while this package gives Python, the gateway, and deployments a stable
module path.
"""

from pathlib import Path

from .main import app, initialize

# Keep detector modules in their established on-disk location while exposing
# them through a valid Python package path.
__path__.append(str(Path(__file__).resolve().parents[1] / "safety-detectors"))

__all__ = ["app", "initialize"]
