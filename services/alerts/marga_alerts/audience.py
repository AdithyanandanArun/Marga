"""Audience targeting — decide which actors should receive each alert.

The resolver uses road-segment matching and spatial proximity to ensure
alerts reach only the actors they are relevant to.  A pothole warning on
a ground-level road is not broadcast to actors on a parallel flyover.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from marga_schemas.alert import Alert
from marga_schemas.common import GeoPoint


def _haversine_m(a: GeoPoint, b: GeoPoint) -> float:
    """Great-circle distance in metres between two GeoPoints."""
    r = 6_371_000  # Earth radius in metres
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlon = math.radians(b.lon - a.lon)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


@dataclass
class AudienceResolver:
    """Determine which actors should receive an alert.

    Two-phase filtering:
    1. **Road-segment relevance**: if both the alert and an actor carry a
       ``road_segment_id``, they must match.
    2. **Spatial proximity**: the actor must be within *radius_m* of the
       alert position.

    Actors already listed in ``alert.affected_actor_ids`` are always included
    (they are the direct participants in the event).
    """

    radius_m: float = 500.0

    def resolve_audience(
        self,
        alert: Alert,
        actors: dict[str, dict[str, Any]],
    ) -> list[str]:
        """Return actor IDs that should receive *alert*.

        Parameters
        ----------
        alert:
            The alert to route.
        actors:
            Mapping of ``actor_id`` → actor metadata dict.  Expected keys:

            - ``position`` — a :class:`GeoPoint` or dict with ``lat``/``lon``
            - ``road_segment_id`` — optional string
            - ``route_segments`` — optional ``list[str]`` of planned route
              segment IDs
        """
        audience: set[str] = set()

        # Direct participants are always included.
        audience.update(alert.affected_actor_ids)

        alert_segment = self._extract_segment(alert)
        alert_pos = alert.position

        for actor_id, meta in actors.items():
            if actor_id in audience:
                continue

            # --- road-segment filter ---
            actor_segment = meta.get("road_segment_id")
            actor_route: list[str] | None = meta.get("route_segments")

            if alert_segment is not None:
                # If the actor has segment info, require overlap.
                if actor_segment is not None and actor_segment != alert_segment:
                    # Actor's current segment differs — check route.
                    if actor_route and alert_segment in actor_route:
                        pass  # will approach the hazard
                    else:
                        continue  # different segment, not on route → skip
                elif actor_segment is None and actor_route:
                    if alert_segment not in actor_route:
                        continue

            # --- spatial proximity filter ---
            if alert_pos is not None:
                actor_pos = self._to_geopoint(meta.get("position"))
                if actor_pos is not None:
                    dist = _haversine_m(alert_pos, actor_pos)
                    if dist > self.radius_m:
                        continue
                # If actor has no position, include conservatively —
                # better to over-alert than to miss.

            audience.add(actor_id)

        return sorted(audience)

    # ---- helpers ----------------------------------------------------------

    @staticmethod
    def _extract_segment(alert: Alert) -> str | None:
        """Pull a road_segment_id from alert machine_reasoning or evidence."""
        seg = alert.machine_reasoning.get("road_segment_id")
        if seg is not None:
            return str(seg)
        return None

    @staticmethod
    def _to_geopoint(pos: Any) -> GeoPoint | None:
        if pos is None:
            return None
        if isinstance(pos, GeoPoint):
            return pos
        if isinstance(pos, dict) and "lat" in pos and "lon" in pos:
            return GeoPoint(lat=pos["lat"], lon=pos["lon"])
        return None
