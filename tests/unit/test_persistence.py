"""Unit tests for marga_persistence — engine, models, and repositories.

These tests use SQLite+aiosqlite to avoid a PostgreSQL dependency in CI.
PostGIS-specific features (Geometry columns, spatial queries) are tested
with plain strings since SQLite has no spatial extension.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from marga_persistence.database import create_engine, get_session
from marga_persistence.models import (
    AlertRow,
    Base,
    HazardObservationRow,
    HazardRow,
    IncidentRow,
    SystemAuditEventRow,
    TrustEventRow,
)
from marga_persistence.repository import (
    AlertRepository,
    AuditRepository,
    HazardRepository,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def engine():
    eng = create_engine("sqlite+aiosqlite://", echo=False)
    # Create tables (skip Geometry DDL — SQLite ignores column types anyway)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as sess:
        yield sess


# ---------------------------------------------------------------------------
# Engine / session tests
# ---------------------------------------------------------------------------


class TestEngine:
    @pytest.mark.asyncio
    async def test_create_engine_returns_async_engine(self):
        eng = create_engine("sqlite+aiosqlite://")
        assert eng is not None
        await eng.dispose()

    @pytest.mark.asyncio
    async def test_get_session_context_manager(self):
        eng = create_engine("sqlite+aiosqlite://")
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with get_session(eng) as sess:
            assert isinstance(sess, AsyncSession)
        await eng.dispose()


# ---------------------------------------------------------------------------
# Model creation tests
# ---------------------------------------------------------------------------


class TestModels:
    def test_hazard_row_defaults(self):
        row = HazardRow(
            hazard_type="POTHOLE",
            position="POINT(77.5946 12.9716)",
            severity=0.7,
            confidence=0.8,
            first_seen=datetime.now(UTC),
            last_seen=datetime.now(UTC),
            ttl_s=300,
        )
        assert row.hazard_id is not None
        assert row.state == "CANDIDATE"
        assert row.evidence_count == 0

    def test_alert_row_defaults(self):
        row = AlertRow(
            alert_type="collision_risk",
            priority="HIGH",
            title="Potential collision",
            description="Two vehicles converging at high speed",
            confidence=0.9,
        )
        assert row.state == "ACTIVE"
        assert row.policy_version == "0.1.0"

    def test_observation_row_creation(self):
        row = HazardObservationRow(
            source_id="sim-001",
            detector_confidence=0.85,
            severity_hint=0.6,
            observed_at=datetime.now(UTC),
        )
        assert row.observation_id is not None

    def test_incident_row_creation(self):
        row = IncidentRow(
            incident_type="COLLISION",
            severity=0.9,
            confidence=0.75,
        )
        assert row.incident_id is not None

    def test_trust_event_row_creation(self):
        row = TrustEventRow(
            sender_id="node-42",
            event_type="REPLAY_REJECTED",
            timestamp=datetime.now(UTC),
        )
        assert row.event_id is not None

    def test_system_audit_event_row_creation(self):
        row = SystemAuditEventRow(
            event_type="SERVICE_START",
            source_service="risk-engine",
            timestamp=datetime.now(UTC),
            trace_id="abc-123",
        )
        assert row.event_id is not None


# ---------------------------------------------------------------------------
# Repository CRUD tests
# ---------------------------------------------------------------------------


class TestHazardRepository:
    @pytest.mark.asyncio
    async def test_save_and_get_hazard(self, session):
        repo = HazardRepository(session)
        now = datetime.now(UTC)
        hazard = HazardRow(
            hazard_type="DEBRIS",
            position="POINT(77.5946 12.9716)",
            severity=0.6,
            confidence=0.8,
            first_seen=now,
            last_seen=now,
            ttl_s=600,
        )
        saved = await repo.save_hazard(hazard)
        await session.commit()

        fetched = await repo.get_hazard(saved.hazard_id)
        assert fetched is not None
        assert fetched.hazard_type == "DEBRIS"
        assert fetched.severity == 0.6

    @pytest.mark.asyncio
    async def test_list_hazards_filter_by_state(self, session):
        repo = HazardRepository(session)
        now = datetime.now(UTC)
        for state in ("CANDIDATE", "VERIFIED", "CANDIDATE"):
            h = HazardRow(
                hazard_type="POTHOLE",
                position="POINT(77.0 13.0)",
                severity=0.5,
                confidence=0.7,
                first_seen=now,
                last_seen=now,
                ttl_s=300,
                state=state,
            )
            await repo.save_hazard(h)
        await session.commit()

        candidates = await repo.list_hazards(state="CANDIDATE")
        assert len(candidates) == 2

        verified = await repo.list_hazards(state="VERIFIED")
        assert len(verified) == 1

    @pytest.mark.asyncio
    async def test_update_hazard(self, session):
        repo = HazardRepository(session)
        now = datetime.now(UTC)
        hazard = HazardRow(
            hazard_type="FLOOD",
            position="POINT(77.0 13.0)",
            severity=0.4,
            confidence=0.6,
            first_seen=now,
            last_seen=now,
            ttl_s=900,
        )
        saved = await repo.save_hazard(hazard)
        await session.commit()

        saved.state = "VERIFIED"
        saved.evidence_count = 3
        updated = await repo.update_hazard(saved)
        await session.commit()

        refetched = await repo.get_hazard(updated.hazard_id)
        assert refetched is not None
        assert refetched.state == "VERIFIED"
        assert refetched.evidence_count == 3

    @pytest.mark.asyncio
    async def test_save_and_get_observations(self, session):
        repo = HazardRepository(session)
        now = datetime.now(UTC)
        hazard = HazardRow(
            hazard_type="STALLED_VEHICLE",
            position="POINT(77.0 13.0)",
            severity=0.5,
            confidence=0.7,
            first_seen=now,
            last_seen=now,
            ttl_s=120,
        )
        saved = await repo.save_hazard(hazard)
        await session.commit()

        obs = HazardObservationRow(
            hazard_id=saved.hazard_id,
            source_id="cam-01",
            detector_confidence=0.9,
            severity_hint=0.5,
            observed_at=now,
        )
        await repo.save_observation(obs)
        await session.commit()

        observations = await repo.get_observations_for_hazard(saved.hazard_id)
        assert len(observations) == 1
        assert observations[0].source_id == "cam-01"

    @pytest.mark.asyncio
    async def test_get_nonexistent_hazard_returns_none(self, session):
        repo = HazardRepository(session)
        result = await repo.get_hazard(uuid.uuid4())
        assert result is None


class TestAlertRepository:
    @pytest.mark.asyncio
    async def test_save_and_get_alert(self, session):
        repo = AlertRepository(session)
        alert = AlertRow(
            alert_type="collision_risk",
            priority="CRITICAL",
            title="Imminent collision",
            description="TTC below 2 seconds",
            confidence=0.95,
        )
        saved = await repo.save_alert(alert)
        await session.commit()

        fetched = await repo.get_alert(saved.alert_id)
        assert fetched is not None
        assert fetched.priority == "CRITICAL"

    @pytest.mark.asyncio
    async def test_list_alerts_filter_by_state(self, session):
        repo = AlertRepository(session)
        for state in ("ACTIVE", "RESOLVED", "ACTIVE"):
            a = AlertRow(
                alert_type="hazard_proximity",
                priority="MEDIUM",
                title="Hazard nearby",
                description="Vehicle approaching known hazard",
                confidence=0.7,
                state=state,
            )
            await repo.save_alert(a)
        await session.commit()

        active = await repo.list_alerts(state="ACTIVE")
        assert len(active) == 2

    @pytest.mark.asyncio
    async def test_update_alert(self, session):
        repo = AlertRepository(session)
        alert = AlertRow(
            alert_type="wrong_way",
            priority="HIGH",
            title="Wrong-way travel detected",
            description="Vehicle heading against traffic flow",
            confidence=0.85,
        )
        saved = await repo.save_alert(alert)
        await session.commit()

        saved.state = "ACKNOWLEDGED"
        updated = await repo.update_alert(saved)
        await session.commit()

        refetched = await repo.get_alert(updated.alert_id)
        assert refetched is not None
        assert refetched.state == "ACKNOWLEDGED"

    @pytest.mark.asyncio
    async def test_get_nonexistent_alert_returns_none(self, session):
        repo = AlertRepository(session)
        result = await repo.get_alert(uuid.uuid4())
        assert result is None


class TestAuditRepository:
    @pytest.mark.asyncio
    async def test_log_and_query_events(self, session):
        repo = AuditRepository(session)
        now = datetime.now(UTC)
        evt = SystemAuditEventRow(
            event_type="ALERT_ISSUED",
            source_service="alert-service",
            timestamp=now,
            detail={"alert_id": "abc-123"},
            trace_id="trace-xyz",
        )
        await repo.log_event(evt)
        await session.commit()

        results = await repo.query_events(event_type="ALERT_ISSUED")
        assert len(results) == 1
        assert results[0].source_service == "alert-service"

    @pytest.mark.asyncio
    async def test_query_by_trace_id(self, session):
        repo = AuditRepository(session)
        now = datetime.now(UTC)
        for i in range(3):
            evt = SystemAuditEventRow(
                event_type=f"EVENT_{i}",
                source_service="test-svc",
                timestamp=now,
                trace_id="common-trace" if i < 2 else "other-trace",
            )
            await repo.log_event(evt)
        await session.commit()

        results = await repo.query_events(trace_id="common-trace")
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_query_with_limit(self, session):
        repo = AuditRepository(session)
        now = datetime.now(UTC)
        for _ in range(10):
            evt = SystemAuditEventRow(
                event_type="BULK",
                source_service="test",
                timestamp=now,
            )
            await repo.log_event(evt)
        await session.commit()

        results = await repo.query_events(event_type="BULK", limit=5)
        assert len(results) == 5


# ---------------------------------------------------------------------------
# Alembic migration syntax check
# ---------------------------------------------------------------------------


class TestMigration:
    def test_migration_module_imports(self):
        """Verify the migration file is valid Python and importable."""
        import importlib.util
        import pathlib

        migration_path = (
            pathlib.Path(__file__).resolve().parents[2]
            / "packages"
            / "persistence"
            / "alembic"
            / "versions"
            / "0001_initial_schema.py"
        )
        spec = importlib.util.spec_from_file_location("migration_0001", migration_path)
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        assert hasattr(mod, "upgrade")
        assert hasattr(mod, "downgrade")
        assert mod.revision == "0001"
