# ADR 002 — Haversine for V2X range, not PostGIS

**Status:** Accepted

## Context

V2X range checks (is an actor within 300 m of a RSU?) happen in the hot path of every world-state ingest. PostGIS `ST_DWithin` is accurate but requires a database round-trip per actor per tick.

## Decision

Use the haversine formula in Python (`_haversine_m` in `canonical_bridge.py`) computed in-process. At city scale (Bangalore Central, <10 km radius) the flat-Earth error is under 0.1 m, which is negligible against the 300 m V2X range.

## Consequences

- No database dependency in the hot ingest path.
- Error grows at large inter-actor distances (>50 km) but V2X is local-area so this is acceptable.
- If Postgres is available in future and sub-metre accuracy matters, replace with PostGIS.
