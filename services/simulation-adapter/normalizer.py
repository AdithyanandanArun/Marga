"""
SumoNormalizer: converts raw SUMO data into canonical schema types.

This is the ONLY place where SUMO-specific data structures touch canonical types.
No SUMO types are exported from this module — only canonical types.
"""

import math
from datetime import datetime
from typing import Optional

from .schemas import (
    DynamicActorObservation,
    InfrastructureState,
    PedestrianState,
    Position,
    PositionEstimate,
    RoadCondition,
    RoadState,
    SignalPhase,
    VehicleState,
    VehicleType,
)

# SUMO type IDs that map to pedestrians and animals respectively.
# These are checked first in normalize_actor() before vehicle mapping.
PEDESTRIAN_TYPE_IDS: frozenset[str] = frozenset({
    "pedestrian", "ped", "person", "walker",
})
ANIMAL_TYPE_IDS: frozenset[str] = frozenset({
    "cow", "dog", "goat", "cattle", "animal",
    "buffalo", "deer", "monkey",
})

# Maps SUMO vehicle type IDs to canonical VehicleType enum values.
# SUMO type IDs are user-defined strings in the route/type files.
DEFAULT_TYPE_MAP: dict[str, VehicleType] = {
    # Generic / default
    "car": VehicleType.car,
    "DEFAULT_VEHTYPE": VehicleType.car,
    "default": VehicleType.car,
    "passenger": VehicleType.car,
    "passenger/sedan": VehicleType.car,
    "passenger/hatchback": VehicleType.car,
    "passenger/wagon": VehicleType.car,
    # Trucks / heavy
    "truck": VehicleType.truck,
    "trailer": VehicleType.truck,
    "heavy": VehicleType.truck,
    # Buses
    "bus": VehicleType.bus,
    "coach": VehicleType.bus,
    "minibus": VehicleType.bus,
    # Motorcycles / two-wheelers
    "motorcycle": VehicleType.motorcycle,
    "moped": VehicleType.motorcycle,
    "scooter": VehicleType.motorcycle,
    # India-specific
    "auto_rickshaw": VehicleType.auto_rickshaw,
    "auto": VehicleType.auto_rickshaw,
    "rickshaw": VehicleType.auto_rickshaw,
    "tuk_tuk": VehicleType.auto_rickshaw,
    "tractor": VehicleType.tractor,
    "agricultural": VehicleType.tractor,
    # Bicycles / non-motorised
    "bicycle": VehicleType.bicycle,
    "bike": VehicleType.bicycle,
    "cyclist": VehicleType.bicycle,
    # Emergency
    "emergency": VehicleType.emergency,
    "ambulance": VehicleType.emergency,
    "fire": VehicleType.emergency,
    "police": VehicleType.emergency,
    "firebrigade": VehicleType.emergency,
}

# Maps SUMO traffic-light phase characters to canonical SignalPhase values.
# SUMO uses a string like "GGrryy" — one char per controlled lane.
# We summarise the whole string to a single dominant phase.
_SUMO_PHASE_CHAR_MAP: dict[str, SignalPhase] = {
    "G": SignalPhase.green,
    "g": SignalPhase.green,        # minor green (yield)
    "r": SignalPhase.red,
    "R": SignalPhase.red,
    "y": SignalPhase.yellow,
    "Y": SignalPhase.yellow,
    "u": SignalPhase.flashing_yellow,  # off, blinking yellow
    "o": SignalPhase.flashing_red,     # off, blinking red
    "O": SignalPhase.off,
}

# Priority order when summarising a multi-lane phase string
_PHASE_PRIORITY: list[SignalPhase] = [
    SignalPhase.green,
    SignalPhase.yellow,
    SignalPhase.flashing_yellow,
    SignalPhase.red,
    SignalPhase.flashing_red,
    SignalPhase.off,
]


class SumoNormalizer:
    """
    Converts raw SUMO simulation data into canonical Marga schema types.

    The normalizer holds the SUMO network origin offset used for
    Cartesian → WGS84 coordinate conversion.  Call ``set_net_offset``
    after connecting to SUMO to initialise the offset.

    SUMO coordinate system:
    - Origin: arbitrary network-local Cartesian (metres)
    - Angles: degrees, counterclockwise from East

    Canonical coordinate system:
    - WGS84 lat/lon
    - Heading: degrees clockwise from True North (0 = North, 90 = East)
    """

    def __init__(
        self,
        net_offset_x: float = 0.0,
        net_offset_y: float = 0.0,
        origin_lat: Optional[float] = None,
        origin_lon: Optional[float] = None,
        type_map: Optional[dict[str, VehicleType]] = None,
    ) -> None:
        """
        Args:
            net_offset_x: SUMO network X offset (from net file or traci).
            net_offset_y: SUMO network Y offset.
            origin_lat: WGS84 latitude of the SUMO coordinate origin.
            origin_lon: WGS84 longitude of the SUMO coordinate origin.
            type_map: Override or extend DEFAULT_TYPE_MAP.
        """
        self._net_offset_x = net_offset_x
        self._net_offset_y = net_offset_y
        self._origin_lat = origin_lat
        self._origin_lon = origin_lon
        self._type_map: dict[str, VehicleType] = {**DEFAULT_TYPE_MAP, **(type_map or {})}

    def set_net_offset(
        self,
        net_offset_x: float,
        net_offset_y: float,
        origin_lat: Optional[float] = None,
        origin_lon: Optional[float] = None,
    ) -> None:
        """Update the network offset after SUMO has started."""
        self._net_offset_x = net_offset_x
        self._net_offset_y = net_offset_y
        if origin_lat is not None:
            self._origin_lat = origin_lat
        if origin_lon is not None:
            self._origin_lon = origin_lon

    # ------------------------------------------------------------------
    # Coordinate conversion
    # ------------------------------------------------------------------

    def sumo_to_wgs84(self, x: float, y: float) -> tuple[float, float]:
        """
        Convert SUMO Cartesian coordinates (metres) to WGS84 (lat, lon).

        Uses a simple flat-Earth approximation centred on the network origin.
        Suitable for city-scale SUMO networks (error < 1 m within ~10 km).

        Returns:
            (lat, lon) tuple in decimal degrees.
        """
        if self._origin_lat is None or self._origin_lon is None:
            # No geo-reference — return raw coordinates flagged as cartesian.
            # Downstream consumers should check the PositionEstimate.source.
            return (y, x)  # treat y as lat-like, x as lon-like

        # Apply net offset to get absolute Cartesian from net origin
        abs_x = x + self._net_offset_x
        abs_y = y + self._net_offset_y

        # Flat-Earth approximation
        # 1 degree latitude ≈ 111,320 m (constant)
        # 1 degree longitude ≈ 111,320 * cos(lat) m
        lat_deg_per_m = 1.0 / 111_320.0
        lon_deg_per_m = 1.0 / (111_320.0 * math.cos(math.radians(self._origin_lat)))

        lat = self._origin_lat + abs_y * lat_deg_per_m
        lon = self._origin_lon + abs_x * lon_deg_per_m
        return (lat, lon)

    # ------------------------------------------------------------------
    # Heading conversion
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_heading(sumo_angle: float) -> float:
        """
        Convert a SUMO angle to canonical heading (degrees clockwise from North).

        SUMO angles: degrees, counterclockwise from East (mathematical convention).
        Canonical heading: degrees, clockwise from True North (compass convention).

        Formula: heading = (90 - sumo_angle) % 360

        Examples:
            SUMO   0° (East)  → canonical  90° (East)
            SUMO  90° (North) → canonical   0° (North)
            SUMO 180° (West)  → canonical 270° (West)
            SUMO 270° (South) → canonical 180° (South)
        """
        return (90.0 - sumo_angle) % 360.0

    # ------------------------------------------------------------------
    # Vehicle type mapping
    # ------------------------------------------------------------------

    def map_vehicle_type(self, sumo_type_id: str) -> VehicleType:
        """Map a SUMO type ID string to a canonical VehicleType."""
        return self._type_map.get(sumo_type_id, VehicleType.car)

    # ------------------------------------------------------------------
    # State normalizers
    # ------------------------------------------------------------------

    def normalize_vehicle_state(
        self,
        vehicle_id: str,
        raw: dict,
        timestamp: datetime,
        scenario_run_id: str,
        source: str,
    ) -> VehicleState:
        """
        Normalize raw SUMO vehicle data into a canonical VehicleState.

        Expected raw dict keys:
            x (float): SUMO Cartesian X position (m)
            y (float): SUMO Cartesian Y position (m)
            speed (float): speed in m/s (passed through unchanged)
            angle (float): SUMO angle (CCW from East, degrees)
            type_id (str): SUMO vehicle type ID
            acceleration (float, optional): acceleration in m/s²
        """
        x: float = raw["x"]
        y: float = raw["y"]
        lat, lon = self.sumo_to_wgs84(x, y)

        heading = self.normalize_heading(raw["angle"])
        vehicle_type = self.map_vehicle_type(raw.get("type_id", "DEFAULT_VEHTYPE"))

        position = PositionEstimate(
            lat=lat,
            lon=lon,
            uncertainty_m=0.0,
            confidence=1.0,
            source=source,
        )

        return VehicleState(
            vehicle_id=vehicle_id,
            timestamp_utc=timestamp,
            position=position,
            speed_mps=raw["speed"],  # SUMO speed is already in m/s
            heading_deg=heading,
            acceleration_mps2=raw.get("acceleration"),
            road_segment_id=raw.get("edge_id"),
            lane_id=raw.get("lane_id"),
            vehicle_type=vehicle_type,
            source=source,
            scenario_run_id=scenario_run_id,
        )

    def normalize_pedestrian_state(
        self,
        ped_id: str,
        raw: dict,
        timestamp: datetime,
        scenario_run_id: str,
        source: str,
    ) -> PedestrianState:
        """
        Normalize raw SUMO pedestrian data into a canonical PedestrianState.

        Expected raw dict keys:
            x (float): SUMO Cartesian X (m)
            y (float): SUMO Cartesian Y (m)
            speed (float): speed in m/s
            angle (float): SUMO angle (CCW from East, degrees)
        """
        x: float = raw["x"]
        y: float = raw["y"]
        lat, lon = self.sumo_to_wgs84(x, y)

        heading = self.normalize_heading(raw["angle"])

        position = PositionEstimate(
            lat=lat,
            lon=lon,
            uncertainty_m=0.0,
            confidence=1.0,
            source=source,
        )

        return PedestrianState(
            pedestrian_id=ped_id,
            timestamp_utc=timestamp,
            position=position,
            speed_mps=raw["speed"],
            heading_deg=heading,
            road_segment_id=raw.get("edge_id"),
            source=source,
            scenario_run_id=scenario_run_id,
        )

    def normalize_actor(
        self,
        actor_id: str,
        raw: dict,
        timestamp: datetime,
        scenario_run_id: str,
        source: str,
    ) -> "VehicleState | PedestrianState | DynamicActorObservation":
        """Dispatch to the correct normalizer based on SUMO type_id.

        Call this instead of normalize_vehicle_state so that pedestrians and
        animals are returned as their correct canonical types rather than being
        mis-cast as vehicles.
        """
        type_id = raw.get("type_id", "").lower()
        if type_id in PEDESTRIAN_TYPE_IDS:
            return self.normalize_pedestrian_state(actor_id, raw, timestamp, scenario_run_id, source)
        if type_id in ANIMAL_TYPE_IDS:
            return self.normalize_dynamic_actor(actor_id, raw, timestamp, scenario_run_id, source)
        return self.normalize_vehicle_state(actor_id, raw, timestamp, scenario_run_id, source)

    def normalize_dynamic_actor(
        self,
        actor_id: str,
        raw: dict,
        timestamp: datetime,
        scenario_run_id: str,
        source: str,
    ) -> DynamicActorObservation:
        """Normalize a raw SUMO animal/unknown actor into DynamicActorObservation.

        Expected raw dict keys:
            x (float): SUMO Cartesian X (m)
            y (float): SUMO Cartesian Y (m)
            speed (float): speed in m/s
            angle (float): SUMO angle (CCW from East, degrees)
            type_id (str): SUMO type ID (e.g. "cow", "dog")
        """
        x: float = raw["x"]
        y: float = raw["y"]
        lat, lon = self.sumo_to_wgs84(x, y)

        return DynamicActorObservation(
            actor_id=actor_id,
            timestamp_utc=timestamp,
            actor_type=raw.get("type_id", "animal"),
            position=PositionEstimate(
                lat=lat,
                lon=lon,
                uncertainty_m=5.0,
                confidence=0.7,
                source=source,
            ),
            confidence=0.7,
            source=source,
            scenario_run_id=scenario_run_id,
        )

    def normalize_signal_state(
        self,
        tls_id: str,
        raw: dict,
        position: tuple[float, float],
        timestamp: datetime,
        scenario_run_id: str,
        source: str,
    ) -> InfrastructureState:
        """
        Normalize raw SUMO traffic-light data into a canonical InfrastructureState.

        Expected raw dict keys:
            phase_string (str): SUMO phase string e.g. "GGrr" (one char per lane)
            phase_remaining_s (float, optional): seconds until next phase switch
            operational (bool, optional): whether the signal is operational

        Args:
            position: (lat, lon) tuple — caller must convert from SUMO Cartesian first.
        """
        phase_string: str = raw.get("phase_string", "")
        dominant_phase = self._summarise_phase_string(phase_string)

        pos = Position(lat=position[0], lon=position[1])

        return InfrastructureState(
            infrastructure_id=tls_id,
            timestamp_utc=timestamp,
            infrastructure_type="traffic_signal",
            position=pos,
            signal_phase=dominant_phase,
            phase_remaining_s=raw.get("phase_remaining_s"),
            operational=raw.get("operational", True),
            source=source,
            scenario_run_id=scenario_run_id,
        )

    def normalize_road_state(
        self,
        edge_id: str,
        raw: dict,
        timestamp: datetime,
        scenario_run_id: str,
        source: str,
    ) -> RoadState:
        """
        Normalize raw SUMO edge data into a canonical RoadState.

        Expected raw dict keys:
            lane_count (int): total number of lanes on this edge
            lanes_available (int, optional): open lanes (defaults to lane_count)
            speed_limit_mps (float): maximum allowed speed in m/s
            road_condition (str, optional): one of "clear","wet","construction","closed"
        """
        total_lanes: int = raw["lane_count"]
        lanes_available: int = raw.get("lanes_available", total_lanes)

        condition_str: str = raw.get("road_condition", "clear")
        try:
            road_condition = RoadCondition(condition_str)
        except ValueError:
            road_condition = RoadCondition.clear

        return RoadState(
            edge_id=edge_id,
            timestamp_utc=timestamp,
            lanes_available=lanes_available,
            total_lanes=total_lanes,
            speed_limit_mps=raw["speed_limit_mps"],
            road_condition=road_condition,
            source=source,
            scenario_run_id=scenario_run_id,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _summarise_phase_string(self, phase_string: str) -> SignalPhase:
        """
        Summarise a multi-lane SUMO phase string into a single SignalPhase.

        Priority: green > yellow > flashing_yellow > red > flashing_red > off
        Returns SignalPhase.off if phase_string is empty or unrecognised.
        """
        if not phase_string:
            return SignalPhase.off

        phases_seen: set[SignalPhase] = set()
        for char in phase_string:
            mapped = _SUMO_PHASE_CHAR_MAP.get(char)
            if mapped is not None:
                phases_seen.add(mapped)

        for phase in _PHASE_PRIORITY:
            if phase in phases_seen:
                return phase

        return SignalPhase.off
