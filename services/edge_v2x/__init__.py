"""Edge V2X service — simulated OBU/ECU nodes with PC5 direct communication.

Provides transport-neutral V2X messaging, local risk evaluation, offline-first
safety delivery, and VRU-aware conflict detection at the edge node level.

This service consumes canonical contracts from marga_schemas and
packages.schemas.canonical. It never creates parallel actor, graph, route,
signal, or risk types.
"""
