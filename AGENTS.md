# Marga — Agent Rules

## Mission

Build an India-ready, software-defined V2X resilience platform that turns live road-user, infrastructure, and hazard signals into explainable, confidence-aware safety alerts. Every decision must survive unreliable positioning, mixed traffic, and intermittent connectivity.

## Hard Rules

1. **No demo-only logic.** No canned outcomes, fixed coordinates, hard-coded actor IDs, or `if demo_case` branches. Every code path must work for arbitrary valid inputs on any imported OSM region.

2. **Canonical contracts first.** Adapters normalize data before it reaches decision logic. Core services never depend on SUMO, frontend, or vendor-specific types. If you need a new field, add it to the canonical schema with a migration note — do not create a parallel type.

3. **Confidence is mandatory.** Safety outputs carry uncertainty, evidence, provenance, and a policy/version basis. Never silently manufacture certainty.

4. **Simulation-reality parity.** Replacing a simulator feed with a real feed must require only adapter/configuration changes, not a rewrite of the core.

5. **Offline-first safety.** Disrupted cloud connectivity must lower certainty or alter delivery paths — it must never silently discard or fabricate data.

6. **Explainable decisions.** Incidents and alerts must be persisted and replayable with their contributing evidence and decision trace.

7. **No Co-authored-by trailers.** Do not add `Co-authored-by` or equivalent co-author tags to any commit.

8. **No credentials in git.** Use environment variables or config files with checked-in `.example` templates. Never commit secrets, keys, or tokens.

9. **Pin dependencies.** Docker images use exact version tags. Python dependencies specify minimum versions in `pyproject.toml`.

10. **Run as non-root.** Containers and long-running services use dedicated service accounts, not root.

## Pre-Push Checklist

Before pushing any branch or opening a PR, verify:

- [ ] `make lint` passes with no errors
- [ ] `make typecheck` passes with no errors
- [ ] `make test` passes — all unit, contract, and integration tests green
- [ ] No `Co-authored-by` trailers in any new commit (`bash infra/scripts/check-coauthor.sh`)
- [ ] No secrets or credentials in staged files
- [ ] New canonical schema fields have contract tests in `tests/contract/`
- [ ] Any new service endpoint has a health check
- [ ] Docker image builds successfully (`docker build .`)
- [ ] Breaking contract changes are documented with a migration note

## Team Ownership

| Owner | Focus |
| --- | --- |
| Adithyan — Lead | Architecture, canonical contracts, integration, release decisions |
| Adithyan — Agent 1 | World state, geospatial/position core, trajectories, collision risk, evidence |
| Adithyan — Agent 2 | Hazard fusion, trust/security, offline transport, persistence, observability, CI/deploy |
| Amrita | OSM/SUMO, simulation and world systems, deterministic scenarios, failure injection |
| Hrishi | Safety policies, acceptance evaluation, false-positive/missed-detection analysis |
| Ali | Control Center, Driver Console, Scenario Studio, live-map UX, E2E verification |
