"""
pytest conftest.py — adds the repo root to sys.path so that
``services.simulation_adapter`` can be imported in tests.
"""
import sys
import os

# Add repo root to path so `services.simulation_adapter` is importable
sys.path.insert(0, os.path.dirname(__file__))
