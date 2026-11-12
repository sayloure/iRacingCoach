"""Reference lap storage, recording and lookup.

A reference lap is a per-distance snapshot of the telemetry the coach compares
the driver against.  It can be produced three ways:

* recorded live from the driver (a clean lap, saved on start/finish crossing);
* loaded from a native iRacing ``.ibt`` telemetry file;
* loaded from a previously saved ``.json`` file in ``laps/``.

Lookups are O(log n) thanks to a sorted ``LapDist`` column and
:func:`bisect.bisect_left`, which is fast enough to call at 60 Hz.
"""

from __future__ import annotations

import bisect
import json
import math
import os
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

import irsdk

from telemetry_reader import TelemetryFrame, _f

LAPS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "laps")

# A reference "brake zone" is any sample where the driver pressed the brake.
BRAKE_THRESHOLD = 15.0      # percent
BRAKE_LOOKAHEAD = 200.0     # metres to scan forward when searching for a zone

_MS_TO_KMH = 3.6
_RAD_TO_DEG = 180.0 / math.pi


@dataclass
class ReferenceLap:
    """Column-oriented store of one reference lap."""

    name: str = "reference"
    track: str = ""
    lap_dist: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float64))
    speed_kmh: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float64))
    throttle: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float64))
    brake: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float64))
    gear: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float64))
    steering_deg: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float64))
    lap_time_at: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float64))
    lap_time: float = 0.0

    def __len__(self) -> int:
        return int(self.lap_dist.size)

    @property
    def is_valid(self) -> bool:
        return self.lap_dist.size > 1

    @property
    def length(self) -> float:
        return float(self.lap_dist[-1]) if self.lap_dist.size else 0.0

    # -- lookup ------------------------------------------------------------

    def index_at(self, lap_dist: float) -> int:
        """Nearest sample index for a distance, clamped to the array."""
        if self.lap_dist.size == 0:
            return 0
        i = bisect.bisect_left(self.lap_dist, float(lap_dist))
        if i <= 0:
            return 0
        if i >= self.lap_dist.size:
            return self.lap_dist.size - 1
        # pick the closer of the two neighbours
        lo = self.lap_dist[i - 1]
        hi = self.lap_dist[i]
        return i if (hi - lap_dist) < (lap_dist - lo) else i - 1

    def sample(self, lap_dist: float) -> dict:
        """Return interpolated values at ``lap_dist`` as a plain dict."""
        if not self.is_valid:
            return {}
        i = self.index_at(lap_dist)
        return {
            "lap_dist": float(self.lap_dist[i]),
            "speed_kmh": float(self.speed_kmh[i]),
            "throttle": float(self.throttle[i]),
            "brake": float(self.brake[i]),
            "gear": int(self.gear[i]),
            "steering_deg": float(self.steering_deg[i]),
            "lap_time_at": self.time_at(lap_dist),
        }

    def time_at(self, lap_dist: float) -> float:
        """Reference lap time (seconds since lap start) at ``lap_dist``.

        Falls back to a linear estimate from ``lap_time`` when per-point
        timing was not stored (older reference files).  Returns ``0.0`` when
        nothing usable is known.
        """
        if not self.is_valid:
            return 0.0
        if self.lap_time_at.size == self.lap_dist.size:
            return float(self.lap_time_at[self.index_at(lap_dist)])
        # Linear fallback: interpolate between 0 and lap_time over the lap.
        length = self.length
        if length > 0.0 and self.lap_time > 0.0:
            return float(self.lap_time) * (float(lap_dist) / length)
        return 0.0

    def brake_zone_time_ahead(self, lap_dist: float) -> Optional[tuple]:
        """Return ``(distance_m, reference_lap_time)`` for the next zone.

        ``reference_lap_time`` is the absolute time on the reference lap at
        which braking starts.  Returns ``None`` when no braking occurs within
        :data:`BRAKE_LOOKAHEAD` metres.
        """
        if not self.is_valid:
            return None
        start = self.index_at(lap_dist)
        limit = lap_dist + BRAKE_LOOKAHEAD
        i = start
        n = self.lap_dist.size
        while i < n and self.lap_dist[i] <= limit:
            if self.brake[i] > BRAKE_THRESHOLD:
                dist = float(self.lap_dist[i] - lap_dist)
                ref_t = (
                    float(self.lap_time_at[i])
                    if self.lap_time_at.size == self.lap_dist.size
                    else self.time_at(self.lap_dist[i])
                )
                return dist, ref_t
            i += 1
        return None

    @classmethod
    def synthetic(
        cls,
        lap_length: float = 5400.0,
        lap_time: float = 105.0,
        samples: int = 1500,
        name: str = "demo-reference",
    ) -> "ReferenceLap":
        """Build a smooth synthetic reference lap (used by ``--demo``).

        The shape mirrors the demo telemetry in :mod:`telemetry_reader` so the
        player and reference traces overlap closely but not exactly, which
        makes the time delta and the brake-zone cue visible right away.
        """
        dist = np.linspace(0.0, lap_length, samples, endpoint=False)
        # Two braking zones per lap, matching the demo reader's corners.
        corner = np.minimum(
            np.abs((dist % (lap_length / 2.0)) - lap_length * 0.2),
            np.abs((dist % (lap_length / 2.0)) - lap_length * 0.45),
        )
        brake = np.clip(100.0 - corner, 0.0, 100.0)
        throttle = np.clip(100.0 - brake * 1.2, 0.0, 100.0)
        speed = np.clip(200.0 - brake * 1.5, 40.0, 320.0)
        steering = 90.0 * np.sin(dist / 120.0)
        gear = np.clip((speed - 120.0) / 25.0 + 2.0, 1.0, 6.0).astype(np.float64)
        lap_time_at = np.linspace(0.0, lap_time, samples, endpoint=False)
        return cls(
            name=name,
            lap_dist=dist,
            speed_kmh=speed,
            throttle=throttle,
            brake=brake,
            gear=gear,
            steering_deg=steering,
            lap_time_at=lap_time_at,
            lap_time=lap_time,
        )

    def brake_zone_ahead(self, lap_dist: float) -> Optional[float]:
        """Distance (metres) to the next brake zone within the look-ahead.

        Returns ``None`` when no braking occurs in the next
        :data:`BRAKE_LOOKAHEAD` metres.
        """
        if not self.is_valid:
            return None
        start = self.index_at(lap_dist)
        limit = lap_dist + BRAKE_LOOKAHEAD
        i = start
        n = self.lap_dist.size
        while i < n and self.lap_dist[i] <= limit:
            if self.brake[i] > BRAKE_THRESHOLD:
                return float(self.lap_dist[i] - lap_dist)
            i += 1
        return None

    def brake_zones(self) -> np.ndarray:
        """All distances where the brake crosses the threshold."""
        if not self.is_valid:
            return np.zeros(0, np.float64)
        mask = self.brake > BRAKE_THRESHOLD
        # keep only the leading edge of each zone
        edges = np.flatnonzero(mask & ~np.roll(mask, 1))
        return self.lap_dist[edges]


class ReferenceManager:
    """Owns the active reference lap plus the recording buffer."""

    def __init__(self, laps_dir: str = LAPS_DIR) -> None:
        self.laps_dir = laps_dir
        os.makedirs(self.laps_dir, exist_ok=True)
        self.reference: Optional[ReferenceLap] = None

        # live recording buffers
        self._recording = False
        self._buf: dict = self._empty_buffer()
        self._last_lap_dist = 0.0
        self._last_lap = -1
        self._lap_start_time = 0.0

    # -- properties --------------------------------------------------------

    @property
    def has_reference(self) -> bool:
        return self.reference is not None and self.reference.is_valid

    @property
    def reference_name(self) -> str:
        return self.reference.name if self.reference else "нет"

    # -- recording ---------------------------------------------------------

    @staticmethod
    def _empty_buffer() -> dict:
        return {
            "lap_dist": [],
            "speed_kmh": [],
            "throttle": [],
            "brake": [],
            "gear": [],
            "steering_deg": [],
            "session_time": [],
        }

    def start_recording(self) -> None:
        self._recording = True
        self._buf = self._empty_buffer()
        self._last_lap_dist = 0.0
        self._last_lap = -1

    def stop_recording(self) -> None:
        self._recording = False

    def feed(self, frame: TelemetryFrame) -> Optional[ReferenceLap]:
        """Feed a live frame while recording.

        Detects the start/finish crossing (``LapDist`` wrapping back towards
        zero on a new lap) and, when a full lap was captured, promotes it to
        the reference if it is faster (or if none exists yet).

        Returns the promoted :class:`ReferenceLap` when a lap was completed.
        """
        if not self._recording or not frame.connected:
            return None

        # Detect lap wrap: LapDist drops sharply or the lap counter increments.
        wrapped = (frame.lap != self._last_lap and self._last_lap >= 0) or (
            frame.lap_dist + 50.0 < self._last_lap_dist
        )
        if wrapped and len(self._buf["lap_dist"]) > 10:
            completed = self._finalise_buffer()
            self._buf = self._empty_buffer()
            self._lap_start_time = frame.session_time
            if completed is not None and self._is_faster(completed):
                self.reference = completed
                self.save_json(completed)
                self._last_lap = frame.lap
                self._last_lap_dist = frame.lap_dist
                return completed
        elif self._last_lap < 0:
            self._lap_start_time = frame.session_time

        self._last_lap = frame.lap
        self._last_lap_dist = frame.lap_dist

        self._buf["lap_dist"].append(frame.lap_dist)
        self._buf["speed_kmh"].append(frame.speed_kmh)
        self._buf["throttle"].append(frame.throttle)
        self._buf["brake"].append(frame.brake)
        self._buf["gear"].append(frame.gear)
        self._buf["steering_deg"].append(frame.steering_deg)
        self._buf["session_time"].append(frame.session_time)
        return None

    def commit_current_lap(self) -> Optional[ReferenceLap]:
        """Force the currently buffered lap to become the reference.

        Used by the "Сделать текущий круг эталоном" button.
        """
        lap = self._finalise_buffer()
        if lap is None:
            return None
        self.reference = lap
        self.save_json(lap)
        return lap

    def _finalise_buffer(self) -> Optional[ReferenceLap]:
        n = len(self._buf["lap_dist"])
        if n < 2:
            return None
        dist = np.asarray(self._buf["lap_dist"], dtype=np.float64)
        order = np.argsort(dist)
        raw_time = np.asarray(self._buf["session_time"], dtype=np.float64)[order]
        if raw_time.size:
            lap_time_at = raw_time - float(raw_time[0])
        else:
            lap_time_at = np.zeros(0, np.float64)
        lap = ReferenceLap(
            name="recorded",
            lap_dist=dist[order],
            speed_kmh=np.asarray(self._buf["speed_kmh"])[order],
            throttle=np.asarray(self._buf["throttle"])[order],
            brake=np.asarray(self._buf["brake"])[order],
            gear=np.asarray(self._buf["gear"])[order],
            steering_deg=np.asarray(self._buf["steering_deg"])[order],
            lap_time_at=lap_time_at,
            lap_time=float(
                self._buf["session_time"][-1] - self._lap_start_time
            ) if self._buf["session_time"] else 0.0,
        )
        return lap if lap.is_valid else None

    def _is_faster(self, lap: ReferenceLap) -> bool:
        if not self.has_reference:
            return True
        if lap.lap_time <= 0 or self.reference.lap_time <= 0:
            return True  # cannot compare -> accept (first clean lap wins)
        return lap.lap_time < self.reference.lap_time

    # -- persistence -------------------------------------------------------

    def save_json(self, lap: Optional[ReferenceLap] = None) -> Optional[str]:
        lap = lap or self.reference
        if lap is None or not lap.is_valid:
            return None
        path = os.path.join(self.laps_dir, "reference.json")
        payload = {
            "name": lap.name,
            "track": lap.track,
            "lap_time": lap.lap_time,
            "lap_dist": lap.lap_dist.tolist(),
            "speed_kmh": lap.speed_kmh.tolist(),
            "throttle": lap.throttle.tolist(),
            "brake": lap.brake.tolist(),
            "gear": lap.gear.tolist(),
            "steering_deg": lap.steering_deg.tolist(),
            "lap_time_at": lap.lap_time_at.tolist(),
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        return path

    def load_json(self, path: str) -> ReferenceLap:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        lap = ReferenceLap(
            name=data.get("name", os.path.basename(path)),
            track=data.get("track", ""),
            lap_dist=np.asarray(data["lap_dist"], dtype=np.float64),
            speed_kmh=np.asarray(data["speed_kmh"], dtype=np.float64),
            throttle=np.asarray(data.get("throttle", []), dtype=np.float64),
            brake=np.asarray(data.get("brake", []), dtype=np.float64),
            gear=np.asarray(data.get("gear", []), dtype=np.float64),
            steering_deg=np.asarray(data.get("steering_deg", []), dtype=np.float64),
            lap_time_at=np.asarray(data.get("lap_time_at", []), dtype=np.float64),
            lap_time=float(data.get("lap_time", 0.0)),
        )
        self.reference = lap
        return lap

    def load_ibt(self, path: str) -> ReferenceLap:
        """Load a single lap from a native iRacing ``.ibt`` file.

        The last full lap contained in the file is used.  ``.ibt`` files store a
        timestamp per record; we slice cleanly between two start/finish
        crossings of ``LapDist``.
        """
        ibt = irsdk.IBT()
        ibt.open(path)
        try:
            lap_dist = np.asarray(ibt.get_all("LapDist"), dtype=np.float64)
            speed = np.asarray(ibt.get_all("Speed"), dtype=np.float64) * _MS_TO_KMH
            throttle = np.asarray(ibt.get_all("Throttle"), dtype=np.float64) * 100.0
            brake = np.asarray(ibt.get_all("Brake"), dtype=np.float64) * 100.0
            gear = np.asarray(ibt.get_all("Gear"), dtype=np.float64)
            steering = (
                np.asarray(ibt.get_all("SteeringWheelAngle"), dtype=np.float64)
                * _RAD_TO_DEG
            )
            session_time = np.asarray(ibt.get_all("SessionTime"), dtype=np.float64)
        finally:
            ibt.close()

        start, end = _last_full_lap(lap_dist)
        sl = slice(start, end)
        lap_time_at = np.zeros(0, np.float64)
        if session_time.size >= end and end > start:
            lap_time_at = session_time[sl] - float(session_time[start])
        lap = ReferenceLap(
            name=os.path.basename(path),
            lap_dist=lap_dist[sl],
            speed_kmh=speed[sl],
            throttle=throttle[sl],
            brake=brake[sl],
            gear=gear[sl],
            steering_deg=steering[sl],
            lap_time_at=lap_time_at,
            lap_time=float(session_time[end - 1] - session_time[start])
            if end > start and session_time.size >= end
            else 0.0,
        )
        self.reference = lap
        return lap

    # -- convenience -------------------------------------------------------

    def load_default(self) -> bool:
        """Load ``laps/reference.json`` if it exists.  Returns success."""
        path = os.path.join(self.laps_dir, "reference.json")
        if os.path.isfile(path):
            try:
                self.load_json(path)
                return True
            except Exception:
                return False
        return False

    def load_synthetic(self, **kwargs) -> ReferenceLap:
        """Install a synthetic reference lap (used by ``--demo``)."""
        lap = ReferenceLap.synthetic(**kwargs)
        self.reference = lap
        return lap


def _last_full_lap(lap_dist: np.ndarray):
    """Return the (start, end) slice of the final complete lap in ``lap_dist``."""
    if lap_dist.size < 2:
        return 0, lap_dist.size
    crosses = np.flatnonzero(lap_dist[1:] < lap_dist[:-1] - 50.0) + 1
    # also treat a low LapDist start as a crossing
    if crosses.size >= 2:
        return int(crosses[-2]), int(crosses[-1])
    if crosses.size == 1:
        return int(crosses[0]), lap_dist.size
    return 0, lap_dist.size