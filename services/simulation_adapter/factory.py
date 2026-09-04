"""
Factory for creating SimulationAdapter instances by backend name.

Usage::

    from simulation_adapter.factory import create_adapter
    from simulation_adapter.normalizer import SumoNormalizer

    normalizer = SumoNormalizer(origin_lat=12.9716, origin_lon=77.5946)
    adapter = create_adapter("traci", normalizer)
"""

from typing import Any

from .base import SimulationAdapter
from .normalizer import SumoNormalizer


def create_adapter(
    backend: str,
    normalizer: SumoNormalizer | None = None,
    **kwargs: Any,
) -> SimulationAdapter:
    """
    Create and return a SimulationAdapter for the specified backend.

    Args:
        backend: One of "traci", "libsumo", "gnss", "obu", "rsu", "phone".
        normalizer: SumoNormalizer instance (required for traci/libsumo backends).
        **kwargs: Backend-specific keyword arguments forwarded to the adapter constructor.

    Returns:
        A SimulationAdapter instance (satisfies the Protocol, not a subclass).

    Raises:
        ValueError: If backend is unknown.
    """
    if backend == "traci":
        from .sumo_traci import SumoTraciAdapter
        return SumoTraciAdapter(normalizer=normalizer)  # type: ignore[return-value]

    if backend == "libsumo":
        from .sumo_libsumo import SumoLibsumoAdapter
        return SumoLibsumoAdapter(normalizer=normalizer)  # type: ignore[return-value]

    if backend == "gnss":
        from .real_adapters import GNSSAdapter
        return GNSSAdapter(**kwargs)  # type: ignore[return-value]

    if backend == "obu":
        from .real_adapters import OBUAdapter
        return OBUAdapter(**kwargs)  # type: ignore[return-value]

    if backend == "rsu":
        from .real_adapters import RSUAdapter
        return RSUAdapter(**kwargs)  # type: ignore[return-value]

    if backend == "phone":
        from .real_adapters import PhoneGPSAdapter
        return PhoneGPSAdapter(**kwargs)  # type: ignore[return-value]

    raise ValueError(
        f"Unknown simulation backend: {backend!r}. "
        "Valid options are: 'traci', 'libsumo', 'gnss', 'obu', 'rsu', 'phone'."
    )
