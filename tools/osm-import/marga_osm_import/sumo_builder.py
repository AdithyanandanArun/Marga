"""SUMO network builder — wraps ``netconvert`` (or ``osmBuild``) via subprocess.

Graceful degradation: if neither tool is on PATH the function returns ``None``
and appends a warning so the caller can proceed with OSM-only data.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


def build_sumo_net(osm_path: Path, output_dir: Path) -> Optional[Path]:
    """Build a SUMO ``.net.xml`` from an OSM file.

    Tries ``netconvert`` first, then ``osmBuild``.  Returns ``None`` with a
    stderr warning if neither tool is available.

    Parameters
    ----------
    osm_path:
        Path to the input ``.osm`` or ``.osm.pbf`` file.
    output_dir:
        Directory where the ``.net.xml`` will be written.

    Returns
    -------
    Optional[Path]
        Path to the generated ``.net.xml``, or ``None`` if SUMO tools are
        not available or the build fails.
    """
    osm_path = Path(osm_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    net_path = output_dir / "road_network.net.xml"

    # ---- Try netconvert --------------------------------------------------
    if shutil.which("netconvert") is not None:
        cmd = [
            "netconvert",
            "--osm-files", str(osm_path),
            "--output-file", str(net_path),
            "--geometry.remove",
            "--roundabouts.guess",
            "--ramps.guess",
            "--junctions.join",
            "--tls.guess-signals",
            "--tls.discard-simple",
            "--tls.join",
            "--type-files", _sumo_type_file(),
            "--no-warnings",
        ]
        print(f"[INFO] Running: {' '.join(cmd)}", file=sys.stderr)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode == 0:
                print(f"[INFO] netconvert succeeded → {net_path}", file=sys.stderr)
                return net_path
            else:
                print(
                    f"[WARNING] netconvert exited with code {result.returncode}:\n"
                    f"{result.stderr[:2000]}",
                    file=sys.stderr,
                )
        except subprocess.TimeoutExpired:
            print("[WARNING] netconvert timed out after 300 s.", file=sys.stderr)
        except Exception as exc:
            print(f"[WARNING] netconvert failed: {exc}", file=sys.stderr)

    # ---- Try osmBuild (older SUMO helper script) -------------------------
    if shutil.which("osmBuild.py") is not None or shutil.which("osmBuild") is not None:
        tool = shutil.which("osmBuild.py") or shutil.which("osmBuild")
        cmd = [
            str(tool),
            "--osm", str(osm_path),
            "--output-dir", str(output_dir),
        ]
        print(f"[INFO] Running osmBuild: {' '.join(cmd)}", file=sys.stderr)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode == 0:
                # osmBuild outputs .net.xml with a different name pattern; find it
                candidates = list(output_dir.glob("*.net.xml"))
                if candidates:
                    found = candidates[0]
                    print(f"[INFO] osmBuild succeeded → {found}", file=sys.stderr)
                    return found
        except Exception as exc:
            print(f"[WARNING] osmBuild failed: {exc}", file=sys.stderr)

    # ---- Neither tool available ------------------------------------------
    print(
        "[WARNING] Neither 'netconvert' nor 'osmBuild' was found on PATH. "
        "SUMO net generation skipped.  Install SUMO (https://sumo.dlr.de) to "
        "enable this step.  Road network will be built from OSM data only.",
        file=sys.stderr,
    )
    return None


def _sumo_type_file() -> str:
    """Return the path to SUMO's OSM type map if available, otherwise empty string."""
    import os

    sumo_home = os.environ.get("SUMO_HOME", "")
    if sumo_home:
        candidate = Path(sumo_home) / "data" / "typemap" / "osmNetconvert.typ.xml"
        if candidate.exists():
            return str(candidate)
    # Fallback — let netconvert use its built-in defaults
    return ""
