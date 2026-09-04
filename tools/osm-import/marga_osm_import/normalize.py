"""Road-graph normalisation — converts raw OSM/SUMO parsed data into canonical
RoadNetwork objects.

Speed limits are always stored in m/s.  Input values may be in km/h (OSM tag
``maxspeed``), already in m/s (SUMO lane speed), or missing — in which case
road-type defaults are applied.
"""

from __future__ import annotations

import math
import sys
from datetime import datetime, timezone
from typing import Optional

from .schema import (
    PedestrianCrossing,
    Position,
    RoadEdge,
    RoadNetwork,
    RoadNode,
    TrafficSignal,
)

# ---------------------------------------------------------------------------
# Default speed limits (m/s) per OSM highway type — India-calibrated
# ---------------------------------------------------------------------------

DEFAULT_SPEED_LIMITS: dict[str, float] = {
    "motorway": 33.33,       # 120 km/h
    "motorway_link": 22.22,  # 80 km/h
    "trunk": 27.78,          # 100 km/h
    "trunk_link": 16.67,     # 60 km/h
    "primary": 13.89,        # 50 km/h
    "primary_link": 11.11,   # 40 km/h
    "secondary": 11.11,      # 40 km/h
    "secondary_link": 8.33,  # 30 km/h
    "tertiary": 8.33,        # 30 km/h
    "tertiary_link": 8.33,   # 30 km/h
    "residential": 5.56,     # 20 km/h
    "living_street": 2.78,   # 10 km/h
    "unclassified": 8.33,    # 30 km/h
    "road": 8.33,            # 30 km/h (generic/unknown)
    "default": 8.33,         # 30 km/h
}

# OSM highway types considered vehicle-accessible (non-pedestrian)
_VEHICLE_HIGHWAY_TYPES: frozenset[str] = frozenset(
    DEFAULT_SPEED_LIMITS.keys()
)

# Pedestrian-only and service-only types we filter out
_EXCLUDED_HIGHWAY_TYPES: frozenset[str] = frozenset(
    {
        "pedestrian",
        "footway",
        "cycleway",
        "path",
        "steps",
        "corridor",
        "track",
        "bridleway",
        "construction",
        "proposed",
        "abandoned",
        "platform",
        "raceway",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _kmh_to_mps(kmh: float) -> float:
    return kmh / 3.6


def _parse_maxspeed(tag_value: str) -> Optional[float]:
    """Parse an OSM ``maxspeed`` tag value and return m/s.

    Handles:
    - Plain numbers (assumed km/h): ``"50"``
    - ``mph`` suffix: ``"30 mph"``
    - ``knots`` suffix: ``"10 knots"``
    - Special values: ``"none"`` / ``"walk"`` / ``"living_street"``
    - Returns *None* if the value is unparseable.
    """
    val = tag_value.strip().lower()
    if not val or val in ("none", "signals", "variable"):
        return None
    if val == "walk":
        return 1.4  # ~5 km/h walking speed
    if val == "living_street":
        return DEFAULT_SPEED_LIMITS["living_street"]

    if "mph" in val:
        try:
            return float(val.replace("mph", "").strip()) * 0.44704
        except ValueError:
            return None
    if "knots" in val or "kn" in val:
        try:
            return float(val.replace("knots", "").replace("kn", "").strip()) * 0.514444
        except ValueError:
            return None

    # Plain numeric — assumed km/h
    try:
        return _kmh_to_mps(float(val))
    except ValueError:
        return None


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in metres between two lat/lon points."""
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _way_length(positions: list[Position]) -> float:
    """Compute total length of a polyline in metres."""
    total = 0.0
    for i in range(1, len(positions)):
        total += _haversine_m(
            positions[i - 1].lat, positions[i - 1].lon,
            positions[i].lat, positions[i].lon,
        )
    return total


# ---------------------------------------------------------------------------
# Main normalisation entry point (OSM parsed dict → RoadNetwork)
# ---------------------------------------------------------------------------

def normalize_road_graph(
    parsed: dict,
    region_name: str,
    bbox: dict,
    sumo_net: Optional[dict] = None,
) -> RoadNetwork:
    """Convert parsed OSM (and optionally SUMO net) data into a canonical ``RoadNetwork``.

    Parameters
    ----------
    parsed:
        Output of ``parser.parse_osm_file``.
    region_name:
        Human-readable region identifier (e.g. ``"Bengaluru Central"``).
    bbox:
        Dict ``{"min_lat", "max_lat", "min_lon", "max_lon"}``.
    sumo_net:
        Optional output of ``parser.parse_sumo_net``; if provided its edge
        geometry and speed data augment the OSM data.

    Returns
    -------
    RoadNetwork
    """
    warnings: list[str] = []
    osm_nodes: dict = parsed.get("nodes", {})
    osm_ways: list[dict] = parsed.get("ways", [])
    signal_node_ids: set[str] = {s["id"] for s in parsed.get("signals", [])}
    crossing_node_ids: set[str] = {c["id"] for c in parsed.get("crossings", [])}

    # ---- Build road nodes ------------------------------------------------
    # Normalise node keys to strings regardless of whether osmium produced int keys
    osm_nodes_str: dict[str, dict] = {str(k): v for k, v in osm_nodes.items()}
    osm_nodes = osm_nodes_str

    node_map: dict[str, RoadNode] = {}
    for nid, ndata in osm_nodes.items():
        node_map[nid] = RoadNode(
            node_id=nid,
            position=Position(lat=ndata["lat"], lon=ndata["lon"]),
        )

    # ---- Build edges from OSM ways ----------------------------------------
    edges: list[RoadEdge] = []
    edge_counter = 0

    # Build a quick lookup: node_id → list of way_ids that touch it (for signals)
    node_to_ways: dict[str, list[str]] = {}
    for way in osm_ways:
        for nref in way["node_refs"]:
            node_to_ways.setdefault(nref, []).append(way["id"])

    for way in osm_ways:
        hw = way["tags"].get("highway", "")
        if hw in _EXCLUDED_HIGHWAY_TYPES:
            continue
        if hw not in _VEHICLE_HIGHWAY_TYPES:
            # Unknown highway type — include with warning
            warnings.append(
                f"Way {way['id']}: unknown highway type '{hw}' — included with defaults."
            )

        node_refs = way["node_refs"]
        if len(node_refs) < 2:
            warnings.append(f"Way {way['id']}: fewer than 2 nodes — skipped.")
            continue

        # Resolve geometry
        positions: list[Position] = []
        for nref in node_refs:
            if nref in osm_nodes:
                n = osm_nodes[nref]
                positions.append(Position(lat=n["lat"], lon=n["lon"]))
            else:
                warnings.append(
                    f"Way {way['id']}: node {nref} not found in OSM data — geometry incomplete."
                )

        if len(positions) < 2:
            warnings.append(f"Way {way['id']}: insufficient resolved geometry — skipped.")
            continue

        # Speed limit
        raw_speed = way["tags"].get("maxspeed", "")
        speed_mps: float
        if raw_speed:
            parsed_speed = _parse_maxspeed(raw_speed)
            if parsed_speed is not None:
                speed_mps = parsed_speed
            else:
                speed_mps = DEFAULT_SPEED_LIMITS.get(hw, DEFAULT_SPEED_LIMITS["default"])
                warnings.append(
                    f"Way {way['id']}: could not parse maxspeed '{raw_speed}' — using default."
                )
        else:
            speed_mps = DEFAULT_SPEED_LIMITS.get(hw, DEFAULT_SPEED_LIMITS["default"])

        # Lane count
        try:
            lanes = int(way["tags"].get("lanes", "1"))
            if lanes < 1:
                lanes = 1
        except ValueError:
            lanes = 1
            warnings.append(f"Way {way['id']}: invalid lane count — defaulting to 1.")

        from_node = node_refs[0]
        to_node = node_refs[-1]
        length_m = _way_length(positions)
        edge_id = f"e_{way['id']}"
        name = way["tags"].get("name") or way["tags"].get("ref") or None

        edges.append(
            RoadEdge(
                edge_id=edge_id,
                osm_way_id=way["id"],
                from_node=from_node,
                to_node=to_node,
                length_m=round(length_m, 3),
                lanes=lanes,
                speed_limit_mps=round(speed_mps, 4),
                road_type=hw if hw else "unclassified",
                name=name,
                geometry=positions,
            )
        )
        edge_counter += 1

    # ---- Traffic signals --------------------------------------------------
    edge_ids_by_node: dict[str, list[str]] = {}
    for edge in edges:
        edge_ids_by_node.setdefault(edge.from_node, []).append(edge.edge_id)
        edge_ids_by_node.setdefault(edge.to_node, []).append(edge.edge_id)

    signals: list[TrafficSignal] = []
    for sig in parsed.get("signals", []):
        nid = sig["id"]
        controlled = edge_ids_by_node.get(nid, [])
        signals.append(
            TrafficSignal(
                signal_id=f"sig_{nid}",
                node_id=nid,
                position=Position(lat=sig["lat"], lon=sig["lon"]),
                controlled_edges=controlled,
            )
        )

    # ---- Pedestrian crossings --------------------------------------------
    crossings: list[PedestrianCrossing] = []
    for cross in parsed.get("crossings", []):
        nid = cross["id"]
        # Find the nearest (first associated) edge
        associated_edges = edge_ids_by_node.get(nid, [])
        edge_id = associated_edges[0] if associated_edges else None
        crossings.append(
            PedestrianCrossing(
                crossing_id=f"cross_{nid}",
                position=Position(lat=cross["lat"], lon=cross["lon"]),
                edge_id=edge_id,
            )
        )

    # ---- Collect all referenced nodes into the node list -----------------
    used_node_ids: set[str] = set()
    for edge in edges:
        used_node_ids.add(edge.from_node)
        used_node_ids.add(edge.to_node)
    for sig in signals:
        used_node_ids.add(sig.node_id)

    nodes: list[RoadNode] = []
    for nid in used_node_ids:
        if nid in node_map:
            nodes.append(node_map[nid])
        else:
            warnings.append(f"Node {nid} referenced but not in OSM data — skipped from node list.")

    if warnings:
        print(f"[INFO] Normalisation produced {len(warnings)} warning(s).", file=sys.stderr)

    return RoadNetwork(
        region_name=region_name,
        bbox=bbox,
        imported_at=datetime.now(tz=timezone.utc),
        edges=edges,
        nodes=nodes,
        signals=signals,
        crossings=crossings,
    )
