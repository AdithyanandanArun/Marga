"""Import report generation, serialisation, and CLI summary output."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .schema import ImportReport, RoadNetwork


def generate_report(
    network: RoadNetwork,
    osm_path: Path,
    net_path: Optional[Path],
    duration_s: float,
    warnings: list[str],
) -> ImportReport:
    """Build an ``ImportReport`` from a completed import run.

    Parameters
    ----------
    network:
        The normalised ``RoadNetwork``.
    osm_path:
        Path to the downloaded ``.osm`` file.
    net_path:
        Path to the SUMO ``.net.xml`` file (or ``None`` if not generated).
    duration_s:
        Wall-clock seconds the import took.
    warnings:
        Warnings accumulated during parsing and normalisation.

    Returns
    -------
    ImportReport
    """
    return ImportReport(
        region_name=network.region_name,
        bbox=network.bbox,
        imported_at=network.imported_at,
        edge_count=len(network.edges),
        node_count=len(network.nodes),
        signal_count=len(network.signals),
        crossing_count=len(network.crossings),
        warnings=warnings,
        osm_file_path=str(osm_path.resolve()),
        net_file_path=str(net_path.resolve()) if net_path else "",
        duration_s=round(duration_s, 3),
    )


def save_report(report: ImportReport, output_dir: Path) -> Path:
    """Serialise ``report`` to ``{output_dir}/import_report.json``.

    Returns the path to the written file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / "import_report.json"
    dest.write_text(
        report.model_dump_json(indent=2),
        encoding="utf-8",
    )
    print(f"[INFO] Import report written to: {dest}", file=sys.stderr)
    return dest


def print_report_summary(report: ImportReport) -> None:
    """Pretty-print a summary of the import report to stdout."""
    sep = "─" * 60
    print(sep)
    print(f"  Marga OSM Import Report")
    print(sep)
    print(f"  Region        : {report.region_name}")
    bbox = report.bbox
    print(
        f"  BBox          : {bbox.get('min_lat'):.4f},{bbox.get('min_lon'):.4f} → "
        f"{bbox.get('max_lat'):.4f},{bbox.get('max_lon'):.4f}"
    )
    print(f"  Imported at   : {report.imported_at.isoformat()}")
    print(f"  Duration      : {report.duration_s:.2f} s")
    print(sep)
    print(f"  Edges         : {report.edge_count}")
    print(f"  Nodes         : {report.node_count}")
    print(f"  Signals       : {report.signal_count}")
    print(f"  Crossings     : {report.crossing_count}")
    print(sep)
    print(f"  OSM file      : {report.osm_file_path}")
    print(f"  Net file      : {report.net_file_path or '(not generated)'}")
    if report.warnings:
        print(sep)
        print(f"  Warnings ({len(report.warnings)}):")
        for w in report.warnings[:10]:
            print(f"    • {w}")
        if len(report.warnings) > 10:
            print(f"    … and {len(report.warnings) - 10} more (see import_report.json)")
    print(sep)
