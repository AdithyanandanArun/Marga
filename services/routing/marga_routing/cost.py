"""Edge cost function for the cooperative routing engine.

Cost = travel_time + congestion_penalty + hazard_penalty + uncertainty_penalty + closure_penalty

All five components are documented in final_imp.md (Adithyan2 scope).
"""

from __future__ import annotations

from .graph import Edge

# Penalty calibration — tuned so that a heavily congested edge costs ~3-5× free flow.
CONGESTION_THRESHOLD: float = 0.70    # capacity ratio below this is uncongested
CONGESTION_SCALE: float = 120.0       # seconds added at full congestion
HAZARD_SCALE: float = 60.0            # seconds added at full hazard
UNCERTAINTY_SCALE: float = 15.0       # seconds added at max GPS uncertainty
CLOSURE_COST: float = 3_600.0         # effectively infinite — 1 hour

# A material reroute is only triggered when the alternative is this much better.
MATERIAL_IMPROVEMENT_RATIO: float = 0.20   # 20 % ETA gain required
CRITICAL_HAZARD_THRESHOLD: float = 0.70    # hazard_penalty that forces reroute


def edge_cost(edge: Edge, *, load_offset: int = 0) -> float:
    """
    Compute the full composite cost for traversing this edge.

    load_offset is the number of vehicles already assigned to this edge by
    the cooperative distributor in the current planning cycle — it allows the
    distributor to simulate capacity pressure without touching live metrics.
    """
    m = edge.metrics

    # Closure: completely impassable
    if m.closure or (m.capacity_ratio >= 1.0 and m.avg_speed_mps < 0.3):
        return CLOSURE_COST

    # 1. Travel time using current speed (from live graph metrics)
    effective_speed = max(0.3, m.avg_speed_mps)
    travel_time = edge.length_m / effective_speed

    # 2. Congestion penalty — grows quadratically above threshold
    effective_ratio = min(1.0, m.capacity_ratio + load_offset / max(1, edge.capacity_vehicles))
    if effective_ratio > CONGESTION_THRESHOLD:
        excess = effective_ratio - CONGESTION_THRESHOLD
        congestion = (excess / (1.0 - CONGESTION_THRESHOLD)) ** 2 * CONGESTION_SCALE
    else:
        congestion = 0.0

    # 3. Hazard penalty (0-1 from graph → seconds of delay)
    hazard = m.hazard_penalty * HAZARD_SCALE

    # 4. GPS uncertainty penalty (low confidence = higher cost)
    uncertainty = max(0.0, 1.0 - m.gps_confidence) * UNCERTAINTY_SCALE

    # 5. Downstream congestion bleeds into cost (reduces speed approaching the edge)
    downstream = m.downstream_congestion * 10.0

    return travel_time + congestion + hazard + uncertainty + downstream


def should_reroute(old_cost: float, new_cost: float, edge_hazard: float, edge_closed: bool) -> tuple[bool, str]:
    """
    Decide whether a reroute is justified and return (trigger, reason).

    Conservative: only reroute when the gain is material to avoid oscillation.
    """
    if edge_closed:
        return True, "closure"

    if edge_hazard >= CRITICAL_HAZARD_THRESHOLD:
        return True, "critical_hazard"

    if old_cost > 0 and (old_cost - new_cost) / old_cost >= MATERIAL_IMPROVEMENT_RATIO:
        return True, "material_eta_improvement"

    return False, ""
