"""OSM data downloader — Overpass API and generic Overpass QL query support."""

from __future__ import annotations

import sys
from pathlib import Path

import requests


# Overpass API endpoint used for bounding-box exports
_OVERPASS_MAP_URL = "https://overpass-api.de/api/map"
# Overpass interpreter for arbitrary QL queries
_OVERPASS_INTERPRETER_URL = "https://overpass-api.de/api/interpreter"

# Warn if the estimated response might be very large (rough heuristic)
_LARGE_AREA_THRESHOLD_DEG2 = 0.5  # ~55 km² at the equator


def _bbox_area(bbox: dict) -> float:
    """Return approximate bounding-box area in square degrees."""
    return (bbox["max_lon"] - bbox["min_lon"]) * (bbox["max_lat"] - bbox["min_lat"])


def download_osm_bbox(bbox: dict, output_path: Path) -> Path:
    """Download raw OSM data for a bounding box via the Overpass ``/api/map`` endpoint.

    Parameters
    ----------
    bbox:
        Dict with keys ``min_lon``, ``min_lat``, ``max_lon``, ``max_lat``.
    output_path:
        Destination file path (e.g. ``./output/region.osm``).

    Returns
    -------
    Path
        Path to the downloaded file (same as *output_path*).
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    area = _bbox_area(bbox)
    if area > _LARGE_AREA_THRESHOLD_DEG2:
        print(
            f"[WARNING] Requested bounding box is large (~{area:.2f} sq°). "
            "The download may be slow or the Overpass API may reject it. "
            "Consider splitting into smaller tiles.",
            file=sys.stderr,
        )

    url = (
        f"{_OVERPASS_MAP_URL}"
        f"?bbox={bbox['min_lon']},{bbox['min_lat']},{bbox['max_lon']},{bbox['max_lat']}"
    )
    print(f"[INFO] Downloading OSM data from: {url}", file=sys.stderr)

    with requests.get(url, stream=True, timeout=300) as response:
        response.raise_for_status()
        total = int(response.headers.get("Content-Length", 0))
        downloaded = 0
        with open(output_path, "wb") as fh:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded / total * 100
                        print(
                            f"\r[INFO] Download progress: {downloaded / 1024:.1f} KB"
                            f" / {total / 1024:.1f} KB ({pct:.1f}%)",
                            end="",
                            file=sys.stderr,
                            flush=True,
                        )
        print(file=sys.stderr)  # newline after progress

    print(
        f"[INFO] Saved OSM data to: {output_path} "
        f"({output_path.stat().st_size / 1024:.1f} KB)",
        file=sys.stderr,
    )
    return output_path


def fetch_overpass_query(query: str, output_path: Path) -> Path:
    """Execute an arbitrary Overpass QL query and save the result.

    Parameters
    ----------
    query:
        Overpass QL query string.
    output_path:
        Destination file path for the XML/JSON response.

    Returns
    -------
    Path
        Path to the saved response file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] Executing Overpass QL query …", file=sys.stderr)

    with requests.post(
        _OVERPASS_INTERPRETER_URL,
        data={"data": query},
        stream=True,
        timeout=300,
    ) as response:
        response.raise_for_status()
        with open(output_path, "wb") as fh:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    fh.write(chunk)

    print(
        f"[INFO] Saved Overpass response to: {output_path} "
        f"({output_path.stat().st_size / 1024:.1f} KB)",
        file=sys.stderr,
    )
    return output_path
