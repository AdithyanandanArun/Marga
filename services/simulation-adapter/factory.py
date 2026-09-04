"""
Factory for creating SimulationAdapter instances by backend name.

Usage::

    from simulation_adapter.factory import create_adapter
    from simulation_adapter.normalizer import SumoNormalizer

    normalizer = SumoNormalizer(origin_lat=12.9716, origin_lon=77.5946)
    adapter = create_adapter("traci", normalizer)
"""

from .base import SimulationAdapter
from .normalizer import SumoNormalizer


def create_adapter(backend: str, normalizer: SumoNormalizer) -> SimulationAdapter:
    """
    Create and return a SimulationAdapter for the specified backend.

    Args:
        backend: "traci" for TCP-based SUMO connection, or
                 "libsumo" for in-process SUMO library.
        normalizer: SumoNormalizer instance (shared between adapter and caller).

    Returns:
        A SimulationAdapter instance (satisfies the Protocol, not a subclass).

    Raises:
        ValueError: If ``backend`` is not "traci" or "libsumo".
    """
    if backend == "traci":
        from .sumo_traci import SumoTraciAdapter

        return SumoTraciAdapter(normalizer=normalizer)  # type: ignore[return-value]

    if backend == "libsumo":
        from .sumo_libsumo import SumoLibsumoAdapter

        return SumoLibsumoAdapter(normalizer=normalizer)  # type: ignore[return-value]

    raise ValueError(
        f"Unknown simulation backend: {backend!r}. "
        "Valid options are: 'traci', 'libsumo'."
    )
