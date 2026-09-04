"""Marga OSM Import CLI.

Usage examples
--------------
Import a region by bounding box::

    marga-osm-import import \\
        --bbox "77.5500,12.9200,77.6500,13.0200" \\
        --region "Bengaluru Central" \\
        --output-dir ./output

Validate an existing road_network.json::

    marga-osm-import validate --network ./output/road_network.json

Show a summary of an existing road_network.json::

    marga-osm-import info --network ./output/road_network.json
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Optional

import click

from .downloader import download_osm_bbox
from .normalize import normalize_road_graph
from .parser import parse_osm_file, parse_sumo_net
from .report import generate_report, print_report_summary, save_report
from .schema import RoadNetwork
from .sumo_builder import build_sumo_net


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_bbox(bbox_str: str) -> dict:
    """Parse ``"min_lon,min_lat,max_lon,max_lat"`` into a dict.

    Raises
    ------
    click.BadParameter
        If the string cannot be parsed or the values are out of range.
    """
    parts = bbox_str.strip().split(",")
    if len(parts) != 4:
        raise click.BadParameter(
            f"Expected exactly 4 comma-separated values (min_lon,min_lat,max_lon,max_lat), "
            f"got {len(parts)}: '{bbox_str}'"
        )
    try:
        min_lon, min_lat, max_lon, max_lat = (float(p.strip()) for p in parts)
    except ValueError as exc:
        raise click.BadParameter(f"All values must be numbers: {exc}")

    if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180):
        raise click.BadParameter("Longitude values must be in [-180, 180].")
    if not (-90 <= min_lat <= 90 and -90 <= max_lat <= 90):
        raise click.BadParameter("Latitude values must be in [-90, 90].")
    if min_lon >= max_lon:
        raise click.BadParameter(f"min_lon ({min_lon}) must be less than max_lon ({max_lon}).")
    if min_lat >= max_lat:
        raise click.BadParameter(f"min_lat ({min_lat}) must be less than max_lat ({max_lat}).")

    return {
        "min_lon": min_lon,
        "min_lat": min_lat,
        "max_lon": max_lon,
        "max_lat": max_lat,
    }


def _load_network(network_path: Path) -> RoadNetwork:
    raw = network_path.read_text(encoding="utf-8")
    return RoadNetwork.model_validate_json(raw)


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
def main() -> None:
    """Marga OSM Import — download, normalise, and persist OSM road data."""


# ---------------------------------------------------------------------------
# import command
# ---------------------------------------------------------------------------

@main.command("import")
@click.option(
    "--bbox",
    required=True,
    metavar="TEXT",
    help='Bounding box as "min_lon,min_lat,max_lon,max_lat" (WGS-84 degrees).',
)
@click.option("--region", required=True, metavar="TEXT", help="Human-readable region name.")
@click.option(
    "--output-dir",
    default="./output",
    show_default=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to write output files.",
)
@click.option(
    "--overwrite",
    is_flag=True,
    default=False,
    help="Overwrite existing output files.",
)
def import_cmd(bbox: str, region: str, output_dir: Path, overwrite: bool) -> None:
    """Download OSM data for BBOX and produce road_network.json + import_report.json."""
    t_start = time.monotonic()
    warnings: list[str] = []

    # 1. Validate bbox
    try:
        bbox_dict = _parse_bbox(bbox)
    except click.BadParameter as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    osm_path = output_dir / f"{_slug(region)}.osm"
    network_path = output_dir / "road_network.json"
    report_path = output_dir / "import_report.json"

    if not overwrite:
        for p in (network_path, report_path):
            if p.exists():
                click.echo(
                    f"Error: {p} already exists. Use --overwrite to replace it.", err=True
                )
                sys.exit(1)

    # 2. Download OSM data
    click.echo(f"[1/5] Downloading OSM data for '{region}' …")
    try:
        download_osm_bbox(bbox_dict, osm_path)
    except Exception as exc:
        click.echo(f"Error downloading OSM data: {exc}", err=True)
        sys.exit(1)

    # 3. Build SUMO net (optional / graceful degradation)
    click.echo("[2/5] Building SUMO network (netconvert) …")
    net_path = build_sumo_net(osm_path, output_dir)
    if net_path is None:
        warnings.append(
            "SUMO netconvert not available — network built from OSM data only."
        )

    # 4. Parse OSM + optional SUMO net
    click.echo("[3/5] Parsing OSM file …")
    parsed_osm = parse_osm_file(osm_path)

    parsed_sumo: Optional[dict] = None
    if net_path is not None and net_path.exists():
        click.echo("[3b/5] Parsing SUMO net …")
        parsed_sumo = parse_sumo_net(net_path)

    # 5. Normalise
    click.echo("[4/5] Normalising road graph …")
    network = normalize_road_graph(parsed_osm, region, bbox_dict, sumo_net=parsed_sumo)

    # 6. Write road_network.json
    click.echo("[5/5] Writing outputs …")
    network_path.write_text(network.model_dump_json(indent=2), encoding="utf-8")
    click.echo(f"      road_network.json  → {network_path}")

    # 7. Write import_report.json
    duration_s = time.monotonic() - t_start
    report = generate_report(network, osm_path, net_path, duration_s, warnings)
    save_report(report, output_dir)
    click.echo(f"      import_report.json → {report_path}")

    # 8. Print summary
    print_report_summary(report)


# ---------------------------------------------------------------------------
# validate command
# ---------------------------------------------------------------------------

@main.command("validate")
@click.option(
    "--network",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to road_network.json.",
)
def validate_cmd(network: Path) -> None:
    """Validate a road_network.json by running smoke checks."""
    click.echo(f"Loading network from: {network}")
    try:
        net = _load_network(network)
    except Exception as exc:
        click.echo(f"Error: failed to load network: {exc}", err=True)
        sys.exit(1)

    errors: list[str] = []
    warnings_v: list[str] = []

    # Schema version check
    if net.schema_version != "1.0":
        warnings_v.append(f"Unexpected schema_version: {net.schema_version}")

    # Basic integrity
    if not net.edges:
        errors.append("No edges found in road network.")
    if not net.nodes:
        errors.append("No nodes found in road network.")

    # Build node id set
    node_ids = {n.node_id for n in net.nodes}

    # Edge references
    for edge in net.edges:
        if edge.from_node not in node_ids:
            warnings_v.append(f"Edge {edge.edge_id}: from_node {edge.from_node} not in nodes.")
        if edge.to_node not in node_ids:
            warnings_v.append(f"Edge {edge.edge_id}: to_node {edge.to_node} not in nodes.")
        if edge.length_m <= 0:
            errors.append(f"Edge {edge.edge_id}: non-positive length {edge.length_m} m.")
        if edge.speed_limit_mps <= 0:
            errors.append(f"Edge {edge.edge_id}: non-positive speed limit {edge.speed_limit_mps} m/s.")
        if edge.lanes < 1:
            errors.append(f"Edge {edge.edge_id}: lane count < 1.")

    # Signal references
    edge_ids = {e.edge_id for e in net.edges}
    for sig in net.signals:
        for eid in sig.controlled_edges:
            if eid not in edge_ids:
                warnings_v.append(
                    f"Signal {sig.signal_id}: controlled edge {eid} not found."
                )

    # Smoke route test: pick first edge and verify geometry is traversable
    if net.edges:
        sample = net.edges[0]
        if len(sample.geometry) < 2:
            warnings_v.append(
                f"Edge {sample.edge_id}: geometry has fewer than 2 points."
            )

    # Report
    sep = "─" * 60
    click.echo(sep)
    click.echo(f"  Validation result for: {net.region_name}")
    click.echo(sep)
    if errors:
        click.echo(f"  ERRORS ({len(errors)}):")
        for e in errors:
            click.echo(f"    ✗ {e}")
    else:
        click.echo("  No errors found.")
    if warnings_v:
        click.echo(f"  WARNINGS ({len(warnings_v)}):")
        for w in warnings_v[:20]:
            click.echo(f"    ⚠ {w}")
    click.echo(sep)

    if errors:
        sys.exit(1)
    click.echo("  Validation passed.")


# ---------------------------------------------------------------------------
# info command
# ---------------------------------------------------------------------------

@main.command("info")
@click.option(
    "--network",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to road_network.json.",
)
def info_cmd(network: Path) -> None:
    """Print a summary of an existing road_network.json."""
    try:
        net = _load_network(network)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    sep = "─" * 60
    click.echo(sep)
    click.echo(f"  Region        : {net.region_name}")
    bbox = net.bbox
    click.echo(
        f"  BBox          : lat [{bbox.get('min_lat')}, {bbox.get('max_lat')}]  "
        f"lon [{bbox.get('min_lon')}, {bbox.get('max_lon')}]"
    )
    click.echo(f"  Imported at   : {net.imported_at.isoformat()}")
    click.echo(f"  Schema version: {net.schema_version}")
    click.echo(sep)
    click.echo(f"  Edges         : {len(net.edges)}")
    click.echo(f"  Nodes         : {len(net.nodes)}")
    click.echo(f"  Signals       : {len(net.signals)}")
    click.echo(f"  Crossings     : {len(net.crossings)}")

    road_type_counts: dict[str, int] = {}
    for edge in net.edges:
        road_type_counts[edge.road_type] = road_type_counts.get(edge.road_type, 0) + 1

    if road_type_counts:
        click.echo(sep)
        click.echo("  Road types:")
        for rtype, count in sorted(road_type_counts.items(), key=lambda x: -x[1]):
            click.echo(f"    {rtype:<25} {count}")
    click.echo(sep)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _slug(text: str) -> str:
    """Convert a region name to a filesystem-safe slug."""
    return (
        text.lower()
        .replace(" ", "_")
        .replace("/", "-")
        .replace("\\", "-")
    )
