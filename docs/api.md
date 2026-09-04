# Marga API Reference

Base URL: `http://localhost:8000`

## World State

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/world-state/ingest` | Ingest adapter events into the live world state |
| `GET` | `/v1/world-state/snapshot` | Pull-based snapshot of all current actors |
| `WS` | `/v1/world-state/stream` | Push-based stream; snapshot on connect, delta on each ingest |
| `POST` | `/v1/world-state/reroute` | Compute perpendicular detour waypoints for an actor |
| `POST` | `/v1/world-state/actors/{id}/command` | Send speed/stop/resume command to an actor |
| `POST` | `/v1/world-state/signals/{id}/command` | Override a traffic signal phase |

## Replay

| Method | Path | Description |
|---|---|---|
| `GET` | `/v1/replay/runs` | List all scenario runs available for replay |
| `GET` | `/v1/replay/{run_id}/events` | Get recorded events for a run (`?from_s=&to_s=&limit=`) |

## Safety (mounted at `/safety`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/safety/v1/evaluate` | Evaluate a list of VehicleState against all detectors |
| `POST` | `/safety/v1/alerts/prioritize` | Prioritize a list of RiskEvents into alerts |

## Scenario Service (port 8001)

| Method | Path | Description |
|---|---|---|
| `GET` | `/v1/scenarios` | List scenarios |
| `POST` | `/v1/scenarios` | Create scenario |
| `POST` | `/v1/scenarios/{id}/runs` | Start a run (launches mock simulation) |
| `GET` | `/v1/runs` | List all runs |
| `GET` | `/v1/runs/{run_id}` | Get run state |
| `POST` | `/v1/runs/{run_id}/pause` | Pause run |
| `POST` | `/v1/runs/{run_id}/resume` | Resume run |
| `POST` | `/v1/runs/{run_id}/stop` | Stop and cancel run |
| `PUT` | `/v1/runs/{run_id}/speed` | Set simulation speed multiplier |
| `POST` | `/v1/runs/{run_id}/inject` | Inject a one-off failure event |
| `GET` | `/v1/scenarios/{id}/runs/{run_id}/events` | Get recorded events for replay |

## Ops

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Aggregate health check |
| `GET` | `/metrics` | Prometheus metrics |
