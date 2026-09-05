# MARGA networking implementation split

MARGA uses two deliberately separate network planes. Direct V2X is the local
safety path; cloud/broker delivery is the wider-area coordination path. They
must not be collapsed into one transport abstraction or made dependent on an
internet connection.

## Shared rules

- All payloads use `marga_schemas.messaging.V2XMessage` and canonical actor
  state. Core networking code must not accept SUMO or frontend-specific types.
- Safety messages carry message ID, timestamp, TTL, priority, policy version,
  evidence, and provenance. The optional `policy_version`, `evidence`, and
  `provenance` fields were added compatibly to `V2XMessage`; existing clients
  remain valid without changes.
- Direct delivery is limited by the measured PC5 range. A node must not learn
  an actor state by a manager-side copy or a cloud-only shortcut.
- Internet loss changes cloud reachability to `DIRECT_ONLY`; it never disables
  in-range PC5 safety delivery.
- Repeated evaluations of the same actor pair must refresh state without
  repeatedly creating alerts. Only a changed active conflict is emitted.

## Adithyan1 — direct V2X data plane

Owns `services/edge_v2x/`, direct-PC5 contract tests, and additive canonical
message-contract changes.

### Implemented responsibilities

- `EdgeV2XTransport` defines `send`, `receive`, `nearby_nodes`,
  `link_quality`, `peer_distance_m`, and `transport_state` so a future real
  C-V2X PC5 adapter can replace `SimulatedPC5Transport`.
- `SimulatedPC5Transport` performs in-range peer discovery, direct delivery,
  per-peer range/link-quality measurement, and reports connectivity state.
- `EdgeV2XManager` creates OBU/ECU nodes and performs an in-range discovery
  handshake. It never copies an actor state to every node.
- `EdgeV2XNode` keeps only direct, in-range peer state; runs the local,
  confidence-aware risk evaluator; prioritises one active risk; and emits
  direct state/risk messages only after PC5 delivery succeeds.
- Direct messages and newly activated risks are observable through the Edge
  V2X service. The node APIs expose local neighbours, including measured
  distance/link quality, and connectivity state.

### Required checks

- Nearby actors exchange state and can produce a local risk.
- Out-of-range actors neither receive state nor influence local risk.
- Internet off yields `DIRECT_ONLY`, leaves `pc5_active=true`, and still
  delivers a critical safety message.
- Repeated identical input emits at most one newly activated risk per node.
- Risk messages contain risk evidence, confidence, policy version, and direct
  transport provenance.

## Adithyan2 — cloud and gateway control plane

Owns `services/messaging/` and the gateway bridge, without modifying local
PC5 discovery/risk behaviour.

- Own cloud/broker transport, priority queues, TTL expiry, deduplication,
  store-and-forward, delivery audit, reconnect behavior, and fault modelling.
- Publish the actual edge data-plane stream through the gateway contracts used
  by the dashboard: `WS /v1/stream/v2x`, `GET /v1/nodes/{id}/neighbours`, and
  `GET /v1/nodes/{id}/connectivity`.
- Critical safety remains PC5-first. Cloud adds coordination/replay but must
  not claim delivery when direct PC5 has no in-range receiver.
- Persist route choice, delivery result, expiry/drop reasons, and connectivity
  transitions for replay and observability.

## Integration boundary

```text
canonical actor state
  -> Adithyan1 EdgeV2XManager / local PC5
  -> direct state + risk events
  -> Adithyan2 gateway bridge / cloud persistence
  -> Control Center live stream
```

The simulator must send canonical actor updates into this boundary. The UI may
render gateway events only; it must never fabricate V2X links, connectivity,
or risks in the browser.
