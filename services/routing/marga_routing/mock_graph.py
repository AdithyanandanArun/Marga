"""Mock Bangalore junction-district graph for demo and testing.

Matches the 4-junction network in networkEngine.ts:
  Hub          (12.9550, 77.6200) — signalised cross
  Roundabout   (12.9550, 77.6240) — 440 m east
  Rail Crossing(12.9550, 77.6160) — 440 m west
  South T      (12.9510, 77.6200) — 440 m south

Intermediate waypoints are added on each approach road so there are
multiple hops and A* has meaningful path alternatives.
"""

from __future__ import annotations

import math

from .graph import Edge, EdgeMetrics, MobilityGraph, Node

# Geographic reference — lat/lon of the hub
HUB_LAT = 12.9550
HUB_LON = 77.6200
METRES_PER_DEG_LAT = 111_320.0
METRES_PER_DEG_LON = 111_320.0 * math.cos(math.radians(HUB_LAT))


def _offset(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    return lat + north_m / METRES_PER_DEG_LAT, lon + east_m / METRES_PER_DEG_LON


def build_mock_graph() -> MobilityGraph:
    g = MobilityGraph()

    # ── Node definitions ────────────────────────────────────────────────
    hub = (HUB_LAT, HUB_LON)
    rbt = _offset(*hub, 0, 440)     # roundabout
    rail = _offset(*hub, 0, -440)   # railway crossing
    south_t = _offset(*hub, -440, 0)  # south T-junction

    # Midpoints between hub and each junction (for multi-hop paths)
    hub_to_rbt_mid = _offset(*hub, 0, 220)
    hub_to_rail_mid = _offset(*hub, 0, -220)
    hub_to_south_mid = _offset(*hub, -220, 0)

    # Extra nodes for detour diversity
    north_bypass = _offset(*hub, 80, 220)
    south_bypass = _offset(*hub, -80, 220)

    nodes = [
        Node("hub",           HUB_LAT, HUB_LON,         "Signalised Hub"),
        Node("roundabout",    *rbt,                       "Roundabout"),
        Node("rail_crossing", *rail,                      "Railway Crossing"),
        Node("south_t",       *south_t,                   "South T-Junction"),
        Node("hub_rbt_mid",   *hub_to_rbt_mid,            "Hub→Rbt Midpoint"),
        Node("hub_rail_mid",  *hub_to_rail_mid,           "Hub→Rail Midpoint"),
        Node("hub_south_mid", *hub_to_south_mid,          "Hub→South Midpoint"),
        Node("north_bypass",  *north_bypass,              "North Bypass"),
        Node("south_bypass",  *south_bypass,              "South Bypass"),
    ]
    for n in nodes:
        g.add_node(n)

    # ── Edge definitions (bidirectional) ─────────────────────────────────
    # Each major road segment gets two edges (each direction) and one alternative
    # bypass so the pathfinder has meaningful choices.

    segments: list[tuple[str, str, float, str]] = [
        # (src, dst, length_m, edge_id_prefix)
        ("hub",           "hub_rbt_mid",    220, "e_hub_rbt_a"),
        ("hub_rbt_mid",   "roundabout",     220, "e_hub_rbt_b"),
        ("roundabout",    "hub_rbt_mid",    220, "e_rbt_hub_a"),
        ("hub_rbt_mid",   "hub",            220, "e_rbt_hub_b"),

        ("hub",           "hub_rail_mid",   220, "e_hub_rail_a"),
        ("hub_rail_mid",  "rail_crossing",  220, "e_hub_rail_b"),
        ("rail_crossing", "hub_rail_mid",   220, "e_rail_hub_a"),
        ("hub_rail_mid",  "hub",            220, "e_rail_hub_b"),

        ("hub",           "hub_south_mid",  220, "e_hub_south_a"),
        ("hub_south_mid", "south_t",        220, "e_hub_south_b"),
        ("south_t",       "hub_south_mid",  220, "e_south_hub_a"),
        ("hub_south_mid", "hub",            220, "e_south_hub_b"),

        # North bypass (longer but avoids hub signal)
        ("hub_rail_mid",  "north_bypass",   180, "e_bypass_n_a"),
        ("north_bypass",  "hub_rbt_mid",    180, "e_bypass_n_b"),
        ("hub_rbt_mid",   "north_bypass",   180, "e_bypass_n_c"),
        ("north_bypass",  "hub_rail_mid",   180, "e_bypass_n_d"),

        # South bypass
        ("hub_south_mid", "south_bypass",   180, "e_bypass_s_a"),
        ("south_bypass",  "hub_rbt_mid",    180, "e_bypass_s_b"),
        ("hub_rbt_mid",   "south_bypass",   180, "e_bypass_s_c"),
        ("south_bypass",  "hub_south_mid",  180, "e_bypass_s_d"),
    ]

    for src, dst, length, eid in segments:
        # Moderately busy default state (Bangalore urban baseline)
        g.add_edge(Edge(
            edge_id=eid,
            src=src,
            dst=dst,
            length_m=length,
            speed_limit_mps=11.1,   # 40 km/h
            lane_count=2,
            capacity_vehicles=40,
            metrics=EdgeMetrics(
                avg_speed_mps=7.5,
                vehicle_count=12,
                capacity_ratio=0.30,
                hazard_penalty=0.0,
                gps_confidence=0.92,
            ),
        ))

    return g
