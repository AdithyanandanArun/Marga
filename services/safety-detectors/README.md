# Marga Safety Detectors Service

FastAPI service for real-time safety feature evaluation, alert generation, and
cooperative hazard fusion on the Marga V2X platform.

This is the core safety lane -- it receives world state snapshots (vehicle
positions, hazard observations, infrastructure states) and produces risk events,
prioritized alerts, and fused hazard records.

## Running the service

From the project root:

```bash
uvicorn services.safety-detectors.main:app --reload --host 0.0.0.0 --port 8100
```

Because the directory name contains a hyphen (which Python cannot use in
dotted imports), the service manipulates `sys.path` internally. You can also
run it directly:

```bash
cd services/safety-detectors
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8100
```

## API Endpoints

| Method | Path                            | Description                                      |
|--------|---------------------------------|--------------------------------------------------|
| GET    | `/health`                       | Health check                                     |
| POST   | `/v1/evaluate`                  | Run all detectors on a world-state snapshot       |
| POST   | `/v1/evaluate/{detector_name}`  | Run a single named detector                      |
| POST   | `/v1/alerts/prioritize`         | Convert risk events into prioritized alerts       |
| POST   | `/v1/hazards/fuse`              | Fuse a new observation with existing hazards      |
| GET    | `/v1/detectors`                 | List registered detectors (name, type, version)   |
| GET    | `/v1/config`                    | Return current PolicyConfig                      |
| PUT    | `/v1/config`                    | Update PolicyConfig at runtime (reinits detectors)|

### Health

```
GET /health
-> {"status": "healthy", "service": "safety-detectors", "version": "0.1.0"}
```

### Evaluate all detectors

```
POST /v1/evaluate
Body: {"world_state": { ... }}
-> {"risks": [...], "detector_count": 10, "errors": []}
```

### Evaluate a single detector

```
POST /v1/evaluate/wrong_way
Body: {"world_state": { ... }}
-> {"risks": [...], "detector_count": 1, "errors": []}
```

### Prioritize alerts

```
POST /v1/alerts/prioritize
Body: {
  "risks": [<RiskEvent>, ...],
  "active_alerts": [<Alert>, ...],
  "actor_states": { ... }
}
-> {"alerts": [<Alert>, ...]}
```

### Fuse hazards

```
POST /v1/hazards/fuse
Body: {
  "observation": { ... },
  "existing_hazards": [{ ... }, ...]
}
-> {"result": { ... }}
```

## Configuration

All detector thresholds are managed through `PolicyConfig`
(`packages/safety_policies/config.py`). The config is loaded with defaults at
startup and can be updated at runtime via `PUT /v1/config`. Updating the config
reinitializes all detectors with the new parameters.

## Detectors

| Module                 | Class                        | Risk Type              | Description                                              |
|------------------------|------------------------------|------------------------|----------------------------------------------------------|
| `wrong_way`            | `WrongWayDetector`           | WRONG_WAY              | Detects vehicles traveling against the expected lane direction |
| `emergency_braking`    | `EmergencyBrakingDetector`   | EMERGENCY_BRAKING      | Detects hard braking events and warns trailing vehicles   |
| `stalled_vehicle`      | `StalledVehicleDetector`     | STALLED_VEHICLE        | Identifies vehicles stopped in a travel lane              |
| `blind_intersection`   | `BlindIntersectionDetector`  | BLIND_INTERSECTION     | Warns of hidden conflicts at unsignalized intersections   |
| `blind_curve`          | `BlindCurveDetector`         | BLIND_CURVE            | Warns of oncoming traffic hidden by road curvature        |
| `emergency_vehicle`    | `EmergencyVehicleDetector`   | EMERGENCY_VEHICLE      | Generates yield alerts for approaching emergency vehicles |
| `animal_conflict`      | `AnimalConflictDetector`     | ANIMAL_CROSSING        | Detects animal/non-connected actor conflict risks         |
| `road_hazard`          | `RoadHazardDetector`         | ROAD_HAZARD            | Assesses risk from road surface and environmental hazards |
| `alert_prioritization` | `AlertPrioritizationDetector`| (various)              | Prioritizes and suppresses alerts per actor               |
| `hazard_fusion`        | `HazardFusionDetector`       | ROAD_HAZARD            | Fuses multi-source hazard observations into canonical hazards |

Detector modules live in `services/safety-detectors/detectors/` and are loaded
dynamically at startup via `importlib`. Missing modules are logged as warnings
and skipped -- the service starts with whatever detectors are available.
