# Marga — Local Runbook

## Quick start (all services)

### Prerequisites
- Python 3.12+, Node 20+, npm 10+
- Docker (optional, for Postgres / Redis / NATS)

### 1. Install Python dependencies
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e packages/schemas -e packages/safety_policies
pip install -e services/gateway -e services/scenario-service
pip install -e services/simulation-adapter
```

### 2. Start infra (optional but needed for persistence)
```bash
# Correct path — NOT infra/compose/docker-compose.yml
docker compose -f infra/docker-compose.yml up -d postgres redis nats
```

### 3. Start the gateway
```bash
uvicorn services.gateway.app:app --reload --port 8000
```

### 4. Start the scenario service
```bash
SCENARIO_GATEWAY_URL=http://localhost:8000 \
uvicorn services.scenario_service.app.main:app --reload --port 8001
```

### 5. Start the frontend
```bash
cd apps/web-dashboard
npm install
npm run dev   # → http://localhost:3000
```

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `SCENARIO_GATEWAY_URL` | `http://localhost:8000` | Gateway URL the scenario-service POSTs events to |
| `SCENARIO_SERVICE_URL` | `http://localhost:8001` | Scenario-service URL used by the gateway's replay proxy |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | Enable OpenTelemetry tracing |

### Common issues

**`make docker-up` fails** — The Makefile points to `infra/compose/docker-compose.yml` which doesn't exist. Use `docker compose -f infra/docker-compose.yml up -d` directly.

**`.venv` from another machine** — Delete and recreate. The venv may contain absolute paths to the original machine's Python.

**Gateway 404 on `/v1/ingest/*`** — Those endpoints are not yet implemented (see objectives.md §1.2). Use `/v1/world-state/ingest` for now.

**Signals not visible** — Zoom in to at least zoom level 12 on the Bangalore Central area (12.97°N, 77.59°E).
