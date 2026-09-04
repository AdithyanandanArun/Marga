"""
File-based persistence for scenario definitions and runs.

Scenarios are stored as individual JSON files under:
  <DATA_DIR>/scenarios/<scenario_id>.json
  <DATA_DIR>/runs/<run_id>.json
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import aiofiles

from .schemas import ScenarioDefinition, ScenarioRun

logger = logging.getLogger(__name__)


class ScenarioNotFoundError(Exception):
    """Raised when a scenario cannot be found in the store."""


class RunNotFoundError(Exception):
    """Raised when a scenario run cannot be found in the store."""


class ScenarioStore:
    """Async file-based persistence for scenario definitions and runs."""

    def __init__(self, base_dir: Path) -> None:
        self._scenarios_dir = base_dir / "scenarios"
        self._runs_dir = base_dir / "runs"
        self._scenarios_dir.mkdir(parents=True, exist_ok=True)
        self._runs_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Scenario operations
    # ------------------------------------------------------------------

    async def save_scenario(self, scenario: ScenarioDefinition) -> str:
        """Persist a scenario definition; returns the scenario_id."""
        path = self._scenarios_dir / f"{scenario.scenario_id}.json"
        async with aiofiles.open(path, "w", encoding="utf-8") as fh:
            await fh.write(scenario.model_dump_json(indent=2))
        logger.debug("Saved scenario %s to %s", scenario.scenario_id, path)
        return scenario.scenario_id

    async def get_scenario(self, scenario_id: str) -> Optional[ScenarioDefinition]:
        """Load a scenario by ID; returns None if not found."""
        path = self._scenarios_dir / f"{scenario_id}.json"
        if not path.exists():
            return None
        async with aiofiles.open(path, "r", encoding="utf-8") as fh:
            raw = await fh.read()
        return ScenarioDefinition.model_validate_json(raw)

    async def list_scenarios(
        self, tag: Optional[str] = None
    ) -> list[ScenarioDefinition]:
        """Return all stored scenarios, optionally filtered by tag."""
        scenarios: list[ScenarioDefinition] = []
        for path in sorted(self._scenarios_dir.glob("*.json")):
            async with aiofiles.open(path, "r", encoding="utf-8") as fh:
                raw = await fh.read()
            try:
                scenario = ScenarioDefinition.model_validate_json(raw)
            except Exception:
                logger.warning("Skipping malformed scenario file: %s", path)
                continue
            if tag is None or tag in scenario.tags:
                scenarios.append(scenario)
        return scenarios

    async def delete_scenario(self, scenario_id: str) -> bool:
        """Delete a scenario by ID; returns True if it existed."""
        path = self._scenarios_dir / f"{scenario_id}.json"
        if not path.exists():
            return False
        path.unlink()
        logger.debug("Deleted scenario %s", scenario_id)
        return True

    async def export_scenario(self, scenario_id: str) -> dict:
        """Return the scenario as a plain dict (full JSON representation)."""
        scenario = await self.get_scenario(scenario_id)
        if scenario is None:
            raise ScenarioNotFoundError(scenario_id)
        return scenario.model_dump(mode="json")

    async def import_scenario(self, data: dict) -> ScenarioDefinition:
        """
        Import a scenario from a plain dict.

        If the incoming scenario_id already exists, a new ID is generated
        so the import never silently overwrites an existing scenario.
        """
        scenario = ScenarioDefinition.model_validate(data)
        existing = await self.get_scenario(scenario.scenario_id)
        if existing is not None:
            # Re-generate ID to avoid clobbering
            import uuid

            scenario = scenario.model_copy(update={"scenario_id": str(uuid.uuid4())})
        await self.save_scenario(scenario)
        return scenario

    # ------------------------------------------------------------------
    # Run operations
    # ------------------------------------------------------------------

    async def save_run(self, run: ScenarioRun) -> str:
        """Persist a new run record; returns the run_id."""
        path = self._runs_dir / f"{run.run_id}.json"
        async with aiofiles.open(path, "w", encoding="utf-8") as fh:
            await fh.write(run.model_dump_json(indent=2))
        logger.debug("Saved run %s to %s", run.run_id, path)
        return run.run_id

    async def get_run(self, run_id: str) -> Optional[ScenarioRun]:
        """Load a run by ID; returns None if not found."""
        path = self._runs_dir / f"{run_id}.json"
        if not path.exists():
            return None
        async with aiofiles.open(path, "r", encoding="utf-8") as fh:
            raw = await fh.read()
        return ScenarioRun.model_validate_json(raw)

    async def update_run(self, run: ScenarioRun) -> None:
        """Overwrite an existing run record (same semantics as save_run)."""
        await self.save_run(run)

    async def list_runs(
        self, scenario_id: Optional[str] = None
    ) -> list[ScenarioRun]:
        """Return all stored runs, optionally filtered by scenario_id."""
        runs: list[ScenarioRun] = []
        for path in sorted(self._runs_dir.glob("*.json")):
            async with aiofiles.open(path, "r", encoding="utf-8") as fh:
                raw = await fh.read()
            try:
                run = ScenarioRun.model_validate_json(raw)
            except Exception:
                logger.warning("Skipping malformed run file: %s", path)
                continue
            if scenario_id is None or run.scenario_id == scenario_id:
                runs.append(run)
        return runs
