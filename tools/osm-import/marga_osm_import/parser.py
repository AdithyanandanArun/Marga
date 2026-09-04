"""OSM and SUMO network parsers.

Two parsing strategies are provided:

1. ``parse_osm_file`` — reads a raw ``.osm`` XML file (or ``.osm.pbf`` if
   *osmium* is installed).  Falls back to the stdlib ``xml.etree.ElementTree``
   for plain XML when osmium is not available.

2. ``parse_sumo_net`` — reads a SUMO ``.net.xml`` file and extracts the edge,
   junction, and traffic-light data used by ``normalize.py``.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# OSM parser
# ---------------------------------------------------------------------------

def _try_osmium(osm_path: Path) -> dict | None:
    """Attempt to parse with osmium; return None if the library is unavailable."""
    try:
        import osmium  # type: ignore[import]
    except ImportError:
        return None

    class _OSMHandler(osmium.SimpleHandler):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__()
            self.nodes: dict[int, dict] = {}
            self.ways: list[dict] = []
            self.signals: list[dict] = []
            self.crossings: list[dict] = []

        def node(self, n: Any) -> None:  # type: ignore[override]
            tags = {tag.k: tag.v for tag in n.tags}
            self.nodes[n.id] = {
                "id": str(n.id),
                "lat": float(n.location.lat),
                "lon": float(n.location.lon),
                "tags": tags,
            }
            if tags.get("highway") == "traffic_signals":
                self.signals.append(
                    {
                        "id": str(n.id),
                        "lat": float(n.location.lat),
                        "lon": float(n.location.lon),
                    }
                )
            if tags.get("highway") == "crossing":
                self.crossings.append(
                    {
                        "id": str(n.id),
                        "lat": float(n.location.lat),
                        "lon": float(n.location.lon),
                    }
                )

        def way(self, w: Any) -> None:  # type: ignore[override]
            tags = {tag.k: tag.v for tag in w.tags}
            if "highway" not in tags:
                return
            node_refs = [str(ref.ref) for ref in w.nodes]
            self.ways.append(
                {
                    "id": str(w.id),
                    "node_refs": node_refs,
                    "tags": tags,
                }
            )

    handler = _OSMHandler()
    try:
        # Use a memory-based location store so coordinates are resolved for plain .osm XML too
        lh = osmium.NodeLocationsForWays(handler)  # type: ignore[attr-defined]
        lh.ignore_errors()
        osmium.apply(osmium.FileProcessor(str(osm_path)), lh)  # type: ignore[attr-defined]
    except Exception:
        # Fallback: apply without location resolution (nodes won't have way-resolved coords,
        # but the nodes dict collected by the `node` callback already holds all positions)
        try:
            handler2 = _OSMHandler()
            handler2.apply_file(str(osm_path))
            handler = handler2
        except Exception:
            return None

    return {
        "nodes": handler.nodes,
        "ways": handler.ways,
        "signals": handler.signals,
        "crossings": handler.crossings,
    }


def _parse_osm_xml(osm_path: Path) -> dict:
    """Parse a plain ``.osm`` XML file using the stdlib ElementTree."""
    tree = ET.parse(str(osm_path))
    root = tree.getroot()

    nodes: dict[str, dict] = {}
    ways: list[dict] = []
    signals: list[dict] = []
    crossings: list[dict] = []

    for elem in root.iter("node"):
        node_id = elem.get("id", "")
        lat_str = elem.get("lat")
        lon_str = elem.get("lon")
        if lat_str is None or lon_str is None:
            continue
        lat = float(lat_str)
        lon = float(lon_str)
        tags = {t.get("k", ""): t.get("v", "") for t in elem.findall("tag")}
        nodes[node_id] = {"id": node_id, "lat": lat, "lon": lon, "tags": tags}

        hw = tags.get("highway", "")
        if hw == "traffic_signals":
            signals.append({"id": node_id, "lat": lat, "lon": lon})
        elif hw == "crossing":
            crossings.append({"id": node_id, "lat": lat, "lon": lon})

    for elem in root.iter("way"):
        tags = {t.get("k", ""): t.get("v", "") for t in elem.findall("tag")}
        if "highway" not in tags:
            continue
        node_refs = [nd.get("ref", "") for nd in elem.findall("nd")]
        ways.append({"id": elem.get("id", ""), "node_refs": node_refs, "tags": tags})

    return {"nodes": nodes, "ways": ways, "signals": signals, "crossings": crossings}


def parse_osm_file(osm_path: Path) -> dict:
    """Parse an OSM file and return a structured dict.

    Tries osmium first (supports .osm.pbf); falls back to stdlib ElementTree
    for plain XML.

    Returns
    -------
    dict with keys:
        ``nodes``     — ``dict[str, {"id", "lat", "lon", "tags"}]``
        ``ways``      — ``list[{"id", "node_refs", "tags"}]``
        ``signals``   — ``list[{"id", "lat", "lon"}]``
        ``crossings`` — ``list[{"id", "lat", "lon"}]``
    """
    osm_path = Path(osm_path)

    # Prefer osmium for .pbf files; attempt for all but fall back gracefully
    result = _try_osmium(osm_path)
    if result is not None:
        print("[INFO] Parsed OSM file with osmium.", file=sys.stderr)
        return result

    print(
        "[INFO] osmium not available — falling back to ElementTree XML parser.",
        file=sys.stderr,
    )
    return _parse_osm_xml(osm_path)


# ---------------------------------------------------------------------------
# SUMO .net.xml parser
# ---------------------------------------------------------------------------

# SUMO internal lane-speed attribute (m/s)
_SUMO_SPEED_ATTR = "speed"


def parse_sumo_net(net_xml_path: Path) -> dict:
    """Parse a SUMO ``.net.xml`` file.

    Returns
    -------
    dict with keys:
        ``edges``         — list of edge dicts
        ``junctions``     — list of junction dicts
        ``traffic_lights``— list of traffic-light dicts
    """
    net_xml_path = Path(net_xml_path)
    tree = ET.parse(str(net_xml_path))
    root = tree.getroot()

    edges: list[dict] = []
    junctions: list[dict] = []
    traffic_lights: list[dict] = []

    for elem in root.iter("edge"):
        edge_id = elem.get("id", "")
        # Skip SUMO internal edges (start with ":")
        if edge_id.startswith(":"):
            continue

        from_node = elem.get("from", "")
        to_node = elem.get("to", "")
        road_type = elem.get("type", "")
        osm_way_id = elem.get("name", None)  # SUMO sometimes puts OSM id here
        priority = elem.get("priority", "")
        func = elem.get("function", "")
        name = elem.get("name", None)

        lanes_data: list[dict] = []
        for lane in elem.findall("lane"):
            speed = float(lane.get(_SUMO_SPEED_ATTR, 0))
            length = float(lane.get("length", 0))
            shape_str = lane.get("shape", "")
            shape_points: list[tuple[float, float]] = []
            if shape_str:
                for pair in shape_str.split(" "):
                    parts = pair.split(",")
                    if len(parts) >= 2:
                        try:
                            x, y = float(parts[0]), float(parts[1])
                            shape_points.append((x, y))
                        except ValueError:
                            pass
            lanes_data.append(
                {"speed_mps": speed, "length_m": length, "shape": shape_points}
            )

        if not lanes_data:
            continue

        avg_speed = sum(ld["speed_mps"] for ld in lanes_data) / len(lanes_data)
        avg_length = sum(ld["length_m"] for ld in lanes_data) / len(lanes_data)

        edges.append(
            {
                "edge_id": edge_id,
                "osm_way_id": osm_way_id,
                "from_node": from_node,
                "to_node": to_node,
                "road_type": road_type,
                "name": name,
                "priority": priority,
                "lane_count": len(lanes_data),
                "speed_limit_mps": avg_speed,
                "length_m": avg_length,
                "lanes_data": lanes_data,
            }
        )

    for elem in root.iter("junction"):
        jtype = elem.get("type", "")
        junctions.append(
            {
                "junction_id": elem.get("id", ""),
                "type": jtype,
                "x": float(elem.get("x", 0)),
                "y": float(elem.get("y", 0)),
            }
        )
        if jtype == "traffic_light":
            traffic_lights.append(
                {
                    "junction_id": elem.get("id", ""),
                    "x": float(elem.get("x", 0)),
                    "y": float(elem.get("y", 0)),
                    "inc_lanes": elem.get("incLanes", "").split(),
                }
            )

    return {
        "edges": edges,
        "junctions": junctions,
        "traffic_lights": traffic_lights,
    }
