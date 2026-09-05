# Mobility graph v0.1 migration

`Hazard.road_segment_id` is an optional canonical association introduced for
the live mobility graph. Existing hazard producers remain valid. OSM/SUMO and
real-road adapters should provide it whenever an observed hazard can be mapped
to a road edge; otherwise the graph records no edge-specific hazard penalty
instead of guessing an association.
