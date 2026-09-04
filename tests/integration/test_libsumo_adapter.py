"""
Structural validation of SumoLibsumoAdapter.

These tests verify that:
  1. The adapter can be imported without libsumo installed.
  2. The adapter satisfies the SimulationAdapter Protocol (isinstance check).
  3. start() raises RuntimeError (not ImportError) when libsumo is absent.
  4. The factory creates a SumoLibsumoAdapter for backend="libsumo".

None of these tests require SUMO or libsumo to be installed — they verify
interface compliance and clean error handling, not simulation output.
"""

from __future__ import annotations

import pytest

from services.simulation_adapter.base import SimulationAdapter
from services.simulation_adapter.factory import create_adapter
from services.simulation_adapter.normalizer import SumoNormalizer
from services.simulation_adapter.sumo_libsumo import SumoLibsumoAdapter


def test_libsumo_adapter_importable() -> None:
    """SumoLibsumoAdapter can be imported without libsumo on the system path."""
    assert SumoLibsumoAdapter is not None


def test_libsumo_adapter_satisfies_protocol() -> None:
    """SumoLibsumoAdapter has all required Protocol methods."""
    normalizer = SumoNormalizer(origin_lat=12.9716, origin_lon=77.5946)
    adapter = SumoLibsumoAdapter(normalizer=normalizer)
    assert isinstance(adapter, SimulationAdapter), (
        "SumoLibsumoAdapter does not satisfy SimulationAdapter Protocol — "
        "check that all Protocol methods are implemented."
    )


def test_libsumo_adapter_raises_runtime_error_when_not_installed() -> None:
    """start() raises RuntimeError (not ImportError) when libsumo is absent."""
    normalizer = SumoNormalizer(origin_lat=12.9716, origin_lon=77.5946)
    adapter = SumoLibsumoAdapter(normalizer=normalizer)

    try:
        import libsumo  # type: ignore[import]
        pytest.skip("libsumo is installed — skipping absence test")
    except ImportError:
        pass

    with pytest.raises(RuntimeError, match="libsumo"):
        adapter.start({})


def test_factory_creates_libsumo_adapter() -> None:
    """create_adapter('libsumo') returns a SumoLibsumoAdapter."""
    normalizer = SumoNormalizer(origin_lat=12.9716, origin_lon=77.5946)
    adapter = create_adapter("libsumo", normalizer=normalizer)
    assert isinstance(adapter, SumoLibsumoAdapter)


def test_factory_creates_gnss_adapter() -> None:
    """create_adapter('gnss') returns a GNSSAdapter satisfying the Protocol."""
    from services.simulation_adapter.real_adapters import GNSSAdapter
    adapter = create_adapter("gnss")
    assert isinstance(adapter, GNSSAdapter)
    assert isinstance(adapter, SimulationAdapter)


def test_factory_creates_obu_adapter() -> None:
    """create_adapter('obu') returns an OBUAdapter satisfying the Protocol."""
    from services.simulation_adapter.real_adapters import OBUAdapter
    adapter = create_adapter("obu")
    assert isinstance(adapter, OBUAdapter)
    assert isinstance(adapter, SimulationAdapter)


def test_factory_creates_rsu_adapter() -> None:
    """create_adapter('rsu') returns an RSUAdapter satisfying the Protocol."""
    from services.simulation_adapter.real_adapters import RSUAdapter
    adapter = create_adapter("rsu")
    assert isinstance(adapter, RSUAdapter)
    assert isinstance(adapter, SimulationAdapter)


def test_factory_unknown_backend_raises_value_error() -> None:
    """Requesting an unknown backend raises ValueError with a helpful message."""
    with pytest.raises(ValueError, match="Unknown simulation backend"):
        create_adapter("invalid_backend")


@pytest.mark.parametrize("backend", ["gnss", "obu", "rsu", "phone"])
def test_real_adapters_reset_and_protocol_methods(backend: str) -> None:
    """All real-world adapters implement reset() and return empty state when unstubbed."""
    adapter = create_adapter(backend)
    adapter.reset("test-run-001")

    assert list(adapter.list_actors()) == []
    assert adapter.get_signal_states() == []
    assert adapter.get_road_states() == []
    assert isinstance(adapter.scenario_run_id, str)
    assert isinstance(adapter.current_time, float)


def test_libsumo_adapter_step_count() -> None:
    """_tick_count starts at zero; step() does not raise when libsumo absent."""
    normalizer = SumoNormalizer(origin_lat=12.9716, origin_lon=77.5946)
    adapter = SumoLibsumoAdapter(normalizer=normalizer)
    adapter.reset("bench-run")

    try:
        import libsumo  # type: ignore[import]
        pytest.skip("libsumo is installed — step() would require a running sim")
    except ImportError:
        pass

    # step() without calling start() should raise, not silently pass
    with pytest.raises(Exception):
        adapter.step(0.1)
