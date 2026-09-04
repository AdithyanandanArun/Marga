# ADR 003 — NATS JetStream instead of Kafka

**Status:** Accepted

## Context

Marga needs an event bus for actor state, risk events, and alerts between services. The main candidates were Kafka and NATS JetStream.

## Decision

Use NATS JetStream.

| Criterion | Kafka | NATS JetStream |
|---|---|---|
| Ops complexity | High (Zookeeper/KRaft) | Low (single binary) |
| Latency | ~5 ms | <1 ms |
| At-least-once delivery | Yes | Yes |
| Consumer groups | Yes | Yes (push/pull consumers) |
| Docker image size | ~650 MB | ~20 MB |
| India offline-first | Needs stable ZK quorum | Runs as cluster-of-one, survives partition |

For a city-scale safety platform the low-latency and offline-first properties of NATS outweigh Kafka's richer ecosystem.

## Consequences

- NATS JetStream is provisioned in `infra/docker-compose.yml`.
- Subject naming convention: `marga.<service>.<entity>.<verb>` e.g. `marga.world.actor.updated`.
- Services that currently use direct HTTP calls should migrate to NATS publish/subscribe once the event bus is wired (see objectives.md §2.1).
