# Live graph-driven signal control

Marga's adaptive signal controller has one strict execution path:

```text
canonical actor and signal telemetry
  -> live mobility graph
  -> registered junction topology
  -> trained RL policy
  -> deterministic signal safety controller
  -> SimulationAdapter / RSU command executor
```

The controller never derives an approach from an edge-name convention and never
applies a signal action without a registered command executor.

## Register topology

An OSM/SUMO adapter must register topology once for every controlled junction.
Use real graph edge IDs and real traffic-light IDs:

```json
{
  "junction_id": "<canonical-junction-id>",
  "signal_id": "<sumo-or-rsu-signal-id>",
  "approaches": [
    {
      "movement_id": "northbound",
      "incoming_edge_ids": ["<edge-id-a>", "<edge-id-b>"],
      "downstream_edge_ids": ["<downstream-edge-id>"],
      "approach_length_m": 120
    }
  ],
  "phase_index_by_name": {"NS_GREEN": 0, "ALL_RED": 1, "EW_GREEN": 2},
  "phase_count": 4,
  "default_phase_duration_s": 25,
  "source": "sumo-traci"
}
```

Send it to `POST /v1/signals/topologies`. Then ingest canonical signal state
at `POST /v1/ingest/signal-state` and actor state through the normal gateway
routes. The controller derives queue, flow, speed, occupancy, pedestrian
demand, VRU density, uncertainty-derived graph confidence, and downstream
congestion from those inputs.

## Operate the controller

- `GET /v1/signals/{junction_id}/state` returns the graph-derived observation.
- `POST /v1/signals/{junction_id}/recommend` records a replayable decision.
- `POST /v1/signals/{junction_id}/apply` applies the latest decision, or a
  supplied `decision_id`, through the registered adapter/RSU executor.
- `GET /v1/signals/decisions/{decision_id}` returns the full evidence and
  policy trace.

Set `MARGA_SIGNAL_CONTROL_ENABLED=true` to run the five-second controller loop
(`MARGA_SIGNAL_CONTROL_INTERVAL_S` changes the interval). It only applies an
action if the runtime has registered a SimulationAdapter or RSU executor; with
no executor it records recommendations without claiming a change was applied.

## Train with SUMO

Use a **dedicated reloadable SUMO process**, never the live demonstration
process. The TraCI process must be listening on its configured remote port and
must permit `load` resets between episodes.

```bash
python -m marga_signal_rl \
  --sumo-host localhost \
  --sumo-port 8813 \
  --junction-id <sumo-tls-id> \
  --episodes 300
```

Training uses the same discrete actions as production: `HOLD`,
`EXTEND_GREEN_5`, `EXTEND_GREEN_10`, and `NEXT_PHASE`. Every proposal is
validated for minimum/maximum green, clearance, and pedestrian safety before
the environment executes it. Persisted policies are loaded with exploration
disabled in the live controller.

Compare a trained policy with fixed-time control using identical scenario seeds
before presenting an improvement. Record average waiting time, trip time,
queue length, throughput, stop count, and safety overrides. Do not claim
live-SUMO performance from the mock-environment comparison.
