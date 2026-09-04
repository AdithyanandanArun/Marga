"""Integration tests for the OSM import tooling.

These tests:
1. Verify the normaliser produces a valid RoadNetwork from sample OSM data.
2. Confirm speed limits are correctly converted from km/h to m/s.
3. Confirm the import report has correct edge/signal/crossing counts.
4. Verify the CLI ``import`` command runs end-to-end without errors using a
   tiny on-disk .osm fixture (no Overpass API calls).
5. Verify that any valid bbox input produces valid canonical output (no
   hard-coded coordinates in the logic).

The sample OSM XML is a hand-crafted fixture representing a small road network
in a generic city area (coordinates are illustrative only and not hard-coded
in the production code paths being tested).
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Sample OSM XML fixture — embedded, no external API call required
# ---------------------------------------------------------------------------

SAMPLE_OSM_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <osm version="0.6">
      <node id="1" lat="12.9716" lon="77.5946"/>
      <node id="2" lat="12.9720" lon="77.5950"/>
      <node id="3" lat="12.9724" lon="77.5946">
        <tag k="highway" v="traffic_signals"/>
      </node>
      <node id="4" lat="12.9720" lon="77.5942">
        <tag k="highway" v="crossing"/>
      </node>
      <way id="101">
        <nd ref="1"/><nd ref="2"/><nd ref="3"/>
        <tag k="highway" v="primary"/>
        <tag k="maxspeed" v="50"/>
        <tag k="lanes" v="2"/>
        <tag k="name" v="MG Road"/>
      </way>
      <way id="102">
        <nd ref="3"/><nd ref="4"/>
        <tag k="highway" v="secondary"/>
        <tag k="maxspeed" v="40"/>
        <tag k="lanes" v="1"/>
      </way>
    </osm>
""")

# Arbitrary bbox used in tests — must NOT match any hard-coded value in source
ARBITRARY_BBOX = {
    "min_lon": 77.59,
    "min_lat": 12.97,
    "max_lon": 77.60,
    "max_lat": 12.98,
}


@pytest.fixture()
def osm_file(tmp_path: Path) -> Path:
    """Write the sample OSM XML to a temp file and return its path."""
    p = tmp_path / "sample.osm"
    p.write_text(SAMPLE_OSM_XML, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

class TestOSMParser:
    def test_parse_returns_expected_keys(self, osm_file: Path) -> None:
        from marga_osm_import.parser import parse_osm_file

        result = parse_osm_file(osm_file)
        assert set(result.keys()) >= {"nodes", "ways", "signals", "crossings"}

    def test_node_count(self, osm_file: Path) -> None:
        from marga_osm_import.parser import parse_osm_file

        result = parse_osm_file(osm_file)
        assert len(result["nodes"]) == 4, "Fixture has 4 OSM nodes"

    def test_way_count(self, osm_file: Path) -> None:
        from marga_osm_import.parser import parse_osm_file

        result = parse_osm_file(osm_file)
        assert len(result["ways"]) == 2, "Fixture has 2 highway ways"

    def test_signal_detected(self, osm_file: Path) -> None:
        from marga_osm_import.parser import parse_osm_file

        result = parse_osm_file(osm_file)
        assert len(result["signals"]) == 1
        assert result["signals"][0]["id"] == "3"

    def test_crossing_detected(self, osm_file: Path) -> None:
        from marga_osm_import.parser import parse_osm_file

        result = parse_osm_file(osm_file)
        assert len(result["crossings"]) == 1
        assert result["crossings"][0]["id"] == "4"

    def test_way_tags_preserved(self, osm_file: Path) -> None:
        from marga_osm_import.parser import parse_osm_file

        result = parse_osm_file(osm_file)
        way_101 = next(w for w in result["ways"] if w["id"] == "101")
        assert way_101["tags"]["highway"] == "primary"
        assert way_101["tags"]["name"] == "MG Road"
        assert way_101["tags"]["maxspeed"] == "50"
        assert way_101["tags"]["lanes"] == "2"


# ---------------------------------------------------------------------------
# Normalizer / speed-limit conversion tests
# ---------------------------------------------------------------------------

class TestNormalizer:
    def _normalize(self, osm_file: Path, bbox: dict | None = None) -> object:
        from marga_osm_import.normalize import normalize_road_graph
        from marga_osm_import.parser import parse_osm_file

        parsed = parse_osm_file(osm_file)
        b = bbox or ARBITRARY_BBOX
        return normalize_road_graph(parsed, "Test Region", b)

    def test_returns_road_network(self, osm_file: Path) -> None:
        from marga_osm_import.schema import RoadNetwork

        network = self._normalize(osm_file)
        assert isinstance(network, RoadNetwork)

    def test_edge_count(self, osm_file: Path) -> None:
        network = self._normalize(osm_file)
        assert len(network.edges) == 2

    def test_signal_count(self, osm_file: Path) -> None:
        network = self._normalize(osm_file)
        assert len(network.signals) == 1

    def test_crossing_count(self, osm_file: Path) -> None:
        network = self._normalize(osm_file)
        assert len(network.crossings) == 1

    def test_primary_road_speed_mps(self, osm_file: Path) -> None:
        """50 km/h must be converted to ~13.89 m/s."""
        network = self._normalize(osm_file)
        primary = next(e for e in network.edges if e.road_type == "primary")
        expected = 50 / 3.6  # 13.888...
        assert abs(primary.speed_limit_mps - expected) < 0.01

    def test_secondary_road_speed_mps(self, osm_file: Path) -> None:
        """40 km/h must be converted to ~11.11 m/s."""
        network = self._normalize(osm_file)
        secondary = next(e for e in network.edges if e.road_type == "secondary")
        expected = 40 / 3.6  # 11.111...
        assert abs(secondary.speed_limit_mps - expected) < 0.01

    def test_speed_limit_is_mps_not_kmh(self, osm_file: Path) -> None:
        """All speed limits must be in m/s (< 50 for any realistic road)."""
        network = self._normalize(osm_file)
        for edge in network.edges:
            assert edge.speed_limit_mps < 50, (
                f"Edge {edge.edge_id}: speed_limit_mps={edge.speed_limit_mps} "
                "looks like km/h, not m/s"
            )

    def test_lane_count(self, osm_file: Path) -> None:
        network = self._normalize(osm_file)
        primary = next(e for e in network.edges if e.road_type == "primary")
        assert primary.lanes == 2

    def test_edge_has_geometry(self, osm_file: Path) -> None:
        network = self._normalize(osm_file)
        for edge in network.edges:
            assert len(edge.geometry) >= 2, f"Edge {edge.edge_id} has insufficient geometry"

    def test_edge_name(self, osm_file: Path) -> None:
        network = self._normalize(osm_file)
        primary = next(e for e in network.edges if e.road_type == "primary")
        assert primary.name == "MG Road"

    def test_region_name_preserved(self, osm_file: Path) -> None:
        from marga_osm_import.normalize import normalize_road_graph
        from marga_osm_import.parser import parse_osm_file

        parsed = parse_osm_file(osm_file)
        network = normalize_road_graph(parsed, "Custom Region XYZ", ARBITRARY_BBOX)
        assert network.region_name == "Custom Region XYZ"

    def test_bbox_preserved(self, osm_file: Path) -> None:
        network = self._normalize(osm_file, bbox=ARBITRARY_BBOX)
        assert network.bbox == ARBITRARY_BBOX

    def test_schema_version(self, osm_file: Path) -> None:
        network = self._normalize(osm_file)
        assert network.schema_version == "1.0"

    def test_any_valid_bbox_produces_valid_network(self, osm_file: Path) -> None:
        """Any valid bbox must produce a non-empty RoadNetwork — no hardcoded coordinates."""
        from marga_osm_import.normalize import normalize_road_graph
        from marga_osm_import.parser import parse_osm_file

        custom_bboxes = [
            {"min_lon": -73.99, "min_lat": 40.73, "max_lon": -73.97, "max_lat": 40.75},
            {"min_lon": 2.29, "min_lat": 48.85, "max_lon": 2.31, "max_lat": 48.87},
            {"min_lon": 103.81, "min_lat": 1.28, "max_lon": 103.85, "max_lat": 1.30},
        ]
        parsed = parse_osm_file(osm_file)
        for bbox in custom_bboxes:
            network = normalize_road_graph(parsed, "Generic Region", bbox)
            assert network.bbox == bbox
            assert isinstance(network.edges, list)
            assert len(network.edges) >= 1


# ---------------------------------------------------------------------------
# Default speed limits
# ---------------------------------------------------------------------------

class TestDefaultSpeedLimits:
    def test_motorway_limit(self) -> None:
        from marga_osm_import.normalize import DEFAULT_SPEED_LIMITS

        assert abs(DEFAULT_SPEED_LIMITS["motorway"] - 33.33) < 0.01

    def test_primary_limit(self) -> None:
        from marga_osm_import.normalize import DEFAULT_SPEED_LIMITS

        assert abs(DEFAULT_SPEED_LIMITS["primary"] - 13.89) < 0.01

    def test_residential_limit(self) -> None:
        from marga_osm_import.normalize import DEFAULT_SPEED_LIMITS

        assert abs(DEFAULT_SPEED_LIMITS["residential"] - 5.56) < 0.01

    def test_all_limits_are_mps(self) -> None:
        from marga_osm_import.normalize import DEFAULT_SPEED_LIMITS

        for road_type, limit in DEFAULT_SPEED_LIMITS.items():
            assert limit < 50, (
                f"DEFAULT_SPEED_LIMITS['{road_type}']={limit} looks like km/h, not m/s"
            )


# ---------------------------------------------------------------------------
# Import report tests
# ---------------------------------------------------------------------------

class TestImportReport:
    def test_report_counts_match_network(self, osm_file: Path) -> None:
        from datetime import datetime, timezone

        from marga_osm_import.normalize import normalize_road_graph
        from marga_osm_import.parser import parse_osm_file
        from marga_osm_import.report import generate_report

        parsed = parse_osm_file(osm_file)
        network = normalize_road_graph(parsed, "Test Region", ARBITRARY_BBOX)
        report = generate_report(network, osm_file, None, duration_s=1.23, warnings=[])

        assert report.edge_count == len(network.edges)
        assert report.signal_count == len(network.signals)
        assert report.crossing_count == len(network.crossings)
        assert report.node_count == len(network.nodes)

    def test_report_schema_version(self, osm_file: Path) -> None:
        from marga_osm_import.normalize import normalize_road_graph
        from marga_osm_import.parser import parse_osm_file
        from marga_osm_import.report import generate_report

        parsed = parse_osm_file(osm_file)
        network = normalize_road_graph(parsed, "Test Region", ARBITRARY_BBOX)
        report = generate_report(network, osm_file, None, duration_s=0.5, warnings=[])
        assert report.schema_version == "1.0"

    def test_report_warnings_propagated(self, osm_file: Path) -> None:
        from marga_osm_import.normalize import normalize_road_graph
        from marga_osm_import.parser import parse_osm_file
        from marga_osm_import.report import generate_report

        parsed = parse_osm_file(osm_file)
        network = normalize_road_graph(parsed, "Test Region", ARBITRARY_BBOX)
        warnings = ["test warning 1", "test warning 2"]
        report = generate_report(network, osm_file, None, duration_s=0.5, warnings=warnings)
        assert report.warnings == warnings

    def test_report_serialises_to_json(self, tmp_path: Path, osm_file: Path) -> None:
        from marga_osm_import.normalize import normalize_road_graph
        from marga_osm_import.parser import parse_osm_file
        from marga_osm_import.report import generate_report, save_report

        parsed = parse_osm_file(osm_file)
        network = normalize_road_graph(parsed, "Test Region", ARBITRARY_BBOX)
        report = generate_report(network, osm_file, None, duration_s=0.5, warnings=[])
        saved = save_report(report, tmp_path)
        assert saved.exists()
        raw = json.loads(saved.read_text())
        assert raw["edge_count"] == report.edge_count
        assert raw["signal_count"] == report.signal_count


# ---------------------------------------------------------------------------
# CLI end-to-end tests (no real Overpass API calls)
# ---------------------------------------------------------------------------

class TestCLI:
    def test_cli_import_with_mocked_download(
        self, tmp_path: Path, osm_file: Path
    ) -> None:
        """The CLI ``import`` command must complete successfully with a mocked downloader."""
        from marga_osm_import.cli import main

        def fake_download(bbox: dict, output_path: Path) -> Path:
            """Copy the sample OSM fixture instead of hitting the network."""
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(osm_file.read_bytes())
            return output_path

        runner = CliRunner()
        with patch("marga_osm_import.cli.download_osm_bbox", side_effect=fake_download), \
             patch("marga_osm_import.cli.build_sumo_net", return_value=None):
            result = runner.invoke(
                main,
                [
                    "import",
                    "--bbox", "77.5900,12.9700,77.6000,12.9800",
                    "--region", "Test City",
                    "--output-dir", str(tmp_path / "out"),
                ],
            )

        assert result.exit_code == 0, f"CLI exited with {result.exit_code}:\n{result.output}"

    def test_cli_import_creates_road_network_json(
        self, tmp_path: Path, osm_file: Path
    ) -> None:
        from marga_osm_import.cli import main

        def fake_download(bbox: dict, output_path: Path) -> Path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(osm_file.read_bytes())
            return output_path

        out_dir = tmp_path / "out"
        runner = CliRunner()
        with patch("marga_osm_import.cli.download_osm_bbox", side_effect=fake_download), \
             patch("marga_osm_import.cli.build_sumo_net", return_value=None):
            runner.invoke(
                main,
                [
                    "import",
                    "--bbox", "77.5900,12.9700,77.6000,12.9800",
                    "--region", "Test City",
                    "--output-dir", str(out_dir),
                ],
            )

        network_file = out_dir / "road_network.json"
        assert network_file.exists(), "road_network.json should be created"
        raw = json.loads(network_file.read_text())
        assert raw["region_name"] == "Test City"
        assert raw["schema_version"] == "1.0"
        assert isinstance(raw["edges"], list)

    def test_cli_import_creates_import_report_json(
        self, tmp_path: Path, osm_file: Path
    ) -> None:
        from marga_osm_import.cli import main

        def fake_download(bbox: dict, output_path: Path) -> Path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(osm_file.read_bytes())
            return output_path

        out_dir = tmp_path / "out"
        runner = CliRunner()
        with patch("marga_osm_import.cli.download_osm_bbox", side_effect=fake_download), \
             patch("marga_osm_import.cli.build_sumo_net", return_value=None):
            runner.invoke(
                main,
                [
                    "import",
                    "--bbox", "77.5900,12.9700,77.6000,12.9800",
                    "--region", "Test City",
                    "--output-dir", str(out_dir),
                ],
            )

        report_file = out_dir / "import_report.json"
        assert report_file.exists(), "import_report.json should be created"
        raw = json.loads(report_file.read_text())
        assert raw["edge_count"] >= 1
        assert raw["signal_count"] >= 0
        assert raw["crossing_count"] >= 0

    def test_cli_info_command(self, tmp_path: Path, osm_file: Path) -> None:
        from marga_osm_import.cli import main

        # First produce a road_network.json
        def fake_download(bbox: dict, output_path: Path) -> Path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(osm_file.read_bytes())
            return output_path

        out_dir = tmp_path / "out"
        runner = CliRunner()
        with patch("marga_osm_import.cli.download_osm_bbox", side_effect=fake_download), \
             patch("marga_osm_import.cli.build_sumo_net", return_value=None):
            runner.invoke(
                main,
                [
                    "import",
                    "--bbox", "77.5900,12.9700,77.6000,12.9800",
                    "--region", "Info Test",
                    "--output-dir", str(out_dir),
                ],
            )

        result = runner.invoke(
            main,
            ["info", "--network", str(out_dir / "road_network.json")],
        )
        assert result.exit_code == 0
        assert "Info Test" in result.output

    def test_cli_validate_command(self, tmp_path: Path, osm_file: Path) -> None:
        from marga_osm_import.cli import main

        def fake_download(bbox: dict, output_path: Path) -> Path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(osm_file.read_bytes())
            return output_path

        out_dir = tmp_path / "out"
        runner = CliRunner()
        with patch("marga_osm_import.cli.download_osm_bbox", side_effect=fake_download), \
             patch("marga_osm_import.cli.build_sumo_net", return_value=None):
            runner.invoke(
                main,
                [
                    "import",
                    "--bbox", "77.5900,12.9700,77.6000,12.9800",
                    "--region", "Validate Test",
                    "--output-dir", str(out_dir),
                ],
            )

        result = runner.invoke(
            main,
            ["validate", "--network", str(out_dir / "road_network.json")],
        )
        assert result.exit_code == 0
        assert "passed" in result.output.lower()

    def test_cli_invalid_bbox_rejected(self) -> None:
        from marga_osm_import.cli import main

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "import",
                "--bbox", "not,a,valid,bbox",
                "--region", "Test",
                "--output-dir", "/tmp/test_out",
            ],
        )
        assert result.exit_code != 0

    def test_cli_bbox_wrong_field_count(self) -> None:
        from marga_osm_import.cli import main

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "import",
                "--bbox", "1.0,2.0",  # only 2 values
                "--region", "Test",
                "--output-dir", "/tmp/test_out",
            ],
        )
        assert result.exit_code != 0

    def test_cli_overwrite_flag(self, tmp_path: Path, osm_file: Path) -> None:
        """Running import twice without --overwrite should fail; with it should succeed."""
        from marga_osm_import.cli import main

        def fake_download(bbox: dict, output_path: Path) -> Path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(osm_file.read_bytes())
            return output_path

        out_dir = tmp_path / "out"
        base_args = [
            "import",
            "--bbox", "77.5900,12.9700,77.6000,12.9800",
            "--region", "Overwrite Test",
            "--output-dir", str(out_dir),
        ]
        runner = CliRunner()

        with patch("marga_osm_import.cli.download_osm_bbox", side_effect=fake_download), \
             patch("marga_osm_import.cli.build_sumo_net", return_value=None):
            # First run — should succeed
            r1 = runner.invoke(main, base_args)
            assert r1.exit_code == 0

            # Second run without --overwrite — should fail
            r2 = runner.invoke(main, base_args)
            assert r2.exit_code != 0

            # Third run with --overwrite — should succeed
            r3 = runner.invoke(main, base_args + ["--overwrite"])
            assert r3.exit_code == 0
