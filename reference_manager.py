"""Reference lap storage, recording and lookup.

A reference lap is a per-distance snapshot of the telemetry the coach compares
the driver against.  It can be produced three ways:

* recorded live from the driver (a clean lap, saved on start/finish crossing);
* loaded from a native iRacing ``.ibt`` telemetry file;
* loaded from a previously saved ``.json`` file in ``laps/``;
* loaded from a ``.csv`` export (Garage61 / MoTeC / Z1).

All loaders normalise to a unified 0..1 percentage coordinate (``lap_dist_pct``),
sorted and duplicate-free.  Real-time lookups use :func:`numpy.interp` for
smooth continuous output at 60 Hz.
"""

from __future__ import annotations

import csv
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

# Fuzzy, case-insensitive column aliases for the popular CSV exports
# (Garage61, iRacing, MoTeC, Z1).  First match wins.
_DIST_M_COLS = ("lapdist", "lap_distance", "lapdistance", "distance", "dist",
                "distance_travelled", "dist_travelled")
_DIST_PCT_COLS = ("lapdistpct", "lap_dist_pct", "distpct", "distancepct",
                  "lap_pct", "pct")
_SPEED_COLS = ("speed", "speedkmh", "speed_kmh", "velocity", "gpsspeed")
_THROTTLE_COLS = ("throttle", "throttlepos", "throttleposition", "thr",
                  "gas", "accelerator")
_BRAKE_COLS = ("brake", "brakepos", "brakeposition", "brk", "brakepressure")
_STEER_COLS = ("steeringwheelangle", "steer", "steering", "steeringangle",
               "steer_angle", "wheelangle")
_GEAR_COLS = ("gear", "gearsel", "gearselected")
_LAPTIME_COLS = ("laptime", "lap_time", "laptime_s", "currentlaptime")
_SESSIONTIME_COLS = ("sessiontime", "session_time", "time", "time_in_session",
                     "sessiontimeofday")
_LAP_COLS = ("lap", "lapno", "lapnumber", "lapnum")

# Sanity bounds used to auto-detect the units of a CSV column.
_SPEED_KMH_CUTOFF = 120.0   # below this we assume the file is in m/s
_FRACTION_CUTOFF = 1.05     # throttle/brake above this are already 0-100
_STEER_RAD_CUTOFF = 10.0    # |angle| below this we assume radians


@dataclass
class ReferenceLap:
    """Column-oriented store of one reference lap.

    All loaders normalise data into two parallel distance grids:

    * ``lap_dist_pct`` – **always** in ``[0.0, 1.0]``, sorted,
      duplicate‑free.  This is the primary coordinate for lookups.
    * ``lap_dist``     – metres on the current lap, either loaded directly
      or computed from ``lap_dist_pct * lap_length``.  Used for
      look‑ahead distance calculations.
    """

    name: str = "reference"
    track: str = ""
    lap_dist: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float64))
    lap_dist_pct: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float64))
    speed_kmh: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float64))
    throttle: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float64))
    brake: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float64))
    gear: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float64))
    steering_deg: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float64))
    lap_time_at: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float64))
    lap_time: float = 0.0
    lap_length: float = 0.0     # track length in metres (0 = unknown)
    dist_is_pct: bool = False   # legacy flag (kept for serialisation compat)

    def __len__(self) -> int:
        return int(self.lap_dist_pct.size)

    @property
    def is_valid(self) -> bool:
        return self.lap_dist_pct.size > 1

    @property
    def length(self) -> float:
        """Track length in metres, or a fallback."""
        if self.lap_length > 0.0:
            return self.lap_length
        if self.lap_dist.size > 0 and float(np.nanmax(self.lap_dist)) > 1.5:
            return float(np.nanmax(self.lap_dist))
        return 0.0

    def _normalise_and_sort(self) -> None:
        """Sort all arrays by ``lap_dist_pct`` and remove duplicates.

        Must be called once after populating the arrays.
        """
        pct = self.lap_dist_pct
        if pct.size == 0 and self.lap_dist.size > 0:
            # Build pct from metres.
            length = self.length
            if length > 0.0:
                pct = np.clip(self.lap_dist / length, 0.0, 1.0)
            else:
                pct = np.linspace(0.0, 1.0, self.lap_dist.size, endpoint=False)
        if pct.size < 2:
            return

        # Normalise to [0, 1] (values like 0-100 -> divide by 100).
        mx = float(np.nanmax(pct))
        if mx > 1.5:
            pct = pct / 100.0
        # Clamp to [0, 1]
        pct = np.clip(pct, 0.0, 1.0).astype(np.float64)

        order = np.argsort(pct, kind="stable")
        pct = pct[order]

        # Remove duplicate pct values (keep first occurrence).
        dup_mask = np.ones(pct.size, dtype=bool)
        dup_mask[1:] = np.diff(pct) > 0.0
        # Edge: keep at least 2 points even if all pct identical.
        if dup_mask.sum() < 2 and pct.size >= 2:
            dup_mask[-1] = True
        pct = pct[dup_mask]

        # Apply the same order + dedup to all arrays.
        for arr_name in ("lap_dist", "speed_kmh", "throttle", "brake",
                         "steering_deg", "gear", "lap_time_at"):
            arr = getattr(self, arr_name, None)
            if arr is not None and arr.size == order.size:
                setattr(self, arr_name, arr[order][dup_mask])

        self.lap_dist_pct = pct

        # Ensure lap_dist (metres) is populated.
        # If lap_dist already contains plausible metre values, keep as-is.
        if self.lap_dist.size == self.lap_dist_pct.size and float(np.nanmax(self.lap_dist)) > 1.5:
            pass  # already in metres
        elif self.lap_length > 0.0:
            self.lap_dist = self.lap_dist_pct * self.lap_length
        else:
            # Unknown track length – default to 4000 m so look-ahead works.
            self.lap_dist = self.lap_dist_pct * 4000.0

    def key_at(self, lap_dist: float, lap_dist_pct: float = 0.0) -> float:
        """Return the lookup key (always normalised 0-1 fraction)."""
        if lap_dist_pct > 0.0:
            return float(lap_dist_pct)
        if lap_dist > 1.0 and self.lap_length > 0.0:
            return float(lap_dist) / self.lap_length
        return float(lap_dist_pct)

    def ensure_length(self, metres: float) -> None:
        """Record a plausible track length and rebuild metre arrays."""
        if metres > 100.0 and self.lap_length <= 0.0:
            self.lap_length = float(metres)
            if self.lap_dist_pct.size > 0:
                self.lap_dist = self.lap_dist_pct * self.lap_length
        elif metres > 100.0 and self.lap_length > 0.0:
            old = self.lap_length
            self.lap_length = float(metres)
            if self.lap_dist.size == self.lap_dist_pct.size:
                self.lap_dist = self.lap_dist * (metres / old)

    # -- lookup ------------------------------------------------------------

    def _interp(self, key: float) -> dict:
        """Linearly interpolate all fields at a normalised *key* (0..1).

        Uses :func:`numpy.interp` for smooth continuous output.
        """
        pct = self.lap_dist_pct
        if pct.size < 2:
            return {}
        # Clamp key to the grid range so interp never extrapolates.
        key_c = float(np.clip(key, pct[0], pct[-1]))
        return {
            "lap_dist": float(np.interp(key_c, pct, self.lap_dist)),
            "speed_kmh": float(np.interp(key_c, pct, self.speed_kmh)),
            "throttle": float(np.interp(key_c, pct, self.throttle)),
            "brake": float(np.interp(key_c, pct, self.brake)),
            "gear": int(round(np.interp(key_c, pct, self.gear))),
            "steering_deg": float(np.interp(key_c, pct, self.steering_deg)),
            "lap_time_at": float(np.interp(key_c, pct, self.lap_time_at)),
        }

    def sample(self, lap_dist: float, lap_dist_pct: float = 0.0) -> dict:
        """Return interpolated values at a distance / fraction as a dict."""
        if not self.is_valid:
            return {}
        key = self.key_at(lap_dist, lap_dist_pct)
        return self._interp(key)

    def time_at(self, lap_dist: float) -> float:
        """Reference lap time (seconds since lap start) at ``lap_dist``."""
        key = self.key_at(lap_dist, 0.0)
        vals = self._interp(key)
        return vals.get("lap_time_at", 0.0)

    def _time_at_key(self, key: float) -> float:
        """Time lookup using an already-resolved lookup key."""
        vals = self._interp(key)
        return vals.get("lap_time_at", 0.0)

    def _zone_distance(self, key: float, zone_key: float) -> float:
        """Metres between a lookup key (0..1) and a zone key (0..1).

        Handles wrap-around across the start/finish line.
        """
        delta = zone_key - key
        if delta < 0.0:
            delta += 1.0  # wrap around
        length = self.length
        if length > 0.0:
            return float(delta) * length
        return float(delta) * 4000.0

    def brake_zone_time_ahead(
        self, lap_dist: float, lap_dist_pct: float = 0.0
    ) -> Optional[tuple]:
        """Return ``(distance_m, reference_lap_time)`` for the next zone.

        ``reference_lap_time`` is the absolute time on the reference lap at
        which braking starts.  Returns ``None`` when no braking occurs within
        :data:`BRAKE_LOOKAHEAD` metres.
        """
        if not self.is_valid:
            return None
        key = self.key_at(lap_dist, lap_dist_pct)
        # Limit in normalised pct units.
        step_pct = BRAKE_LOOKAHEAD / self.length if self.length > 0.0 else 0.05
        limit = key + step_pct
        pct_grid = self.lap_dist_pct
        n = pct_grid.size
        for i in range(n):
            pct_i = pct_grid[i]
            # Check if this point is within the look-ahead window (with wrap).
            if pct_i >= key and pct_i <= limit:
                pass  # forward in same lap segment
            elif pct_i < key and pct_i <= (limit - 1.0):
                pass  # wrapped around
            else:
                if pct_i > limit and key < 0.5:
                    break  # past the look-ahead, no wrap needed
                continue
            if self.brake[i] > BRAKE_THRESHOLD:
                dist = self._zone_distance(key, float(pct_i))
                ref_t = float(np.interp(pct_i, pct_grid, self.lap_time_at))
                return dist, ref_t
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
        lap_dist_pct = np.linspace(0.0, 1.0, samples, endpoint=False)
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
            lap_length=lap_length,
            lap_dist=dist,
            lap_dist_pct=lap_dist_pct,
            speed_kmh=speed,
            throttle=throttle,
            brake=brake,
            gear=gear,
            steering_deg=steering,
            lap_time_at=lap_time_at,
            lap_time=lap_time,
        )

    def brake_zone_ahead(
        self, lap_dist: float, lap_dist_pct: float = 0.0
    ) -> Optional[float]:
        """Distance (metres) to the next brake zone within the look-ahead.

        Returns ``None`` when no braking occurs in the next
        :data:`BRAKE_LOOKAHEAD` metres.  Wraps around start/finish.
        """
        zone = self.brake_zone_time_ahead(lap_dist, lap_dist_pct)
        if zone is not None:
            return zone[0]
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
        lap._normalise_and_sort()
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
            "lap_dist_pct": lap.lap_dist_pct.tolist(),
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
        lap_dist = np.asarray(data["lap_dist"], dtype=np.float64)
        lap_dist_pct = np.asarray(data.get("lap_dist_pct", []), dtype=np.float64)
        lap = ReferenceLap(
            name=data.get("name", os.path.basename(path)),
            track=data.get("track", ""),
            lap_dist=lap_dist,
            lap_dist_pct=lap_dist_pct,
            speed_kmh=np.asarray(data["speed_kmh"], dtype=np.float64),
            throttle=np.asarray(data.get("throttle", []), dtype=np.float64),
            brake=np.asarray(data.get("brake", []), dtype=np.float64),
            gear=np.asarray(data.get("gear", []), dtype=np.float64),
            steering_deg=np.asarray(data.get("steering_deg", []), dtype=np.float64),
            lap_time_at=np.asarray(data.get("lap_time_at", []), dtype=np.float64),
            lap_time=float(data.get("lap_time", 0.0)),
        )
        lap._normalise_and_sort()
        self.reference = lap
        return lap

    def load_csv(self, path: str) -> ReferenceLap:
        """Load a reference lap from a CSV export (Garage61 / MoTeC / Z1)."""
        headers, data = _read_csv_rows(path)
        if not data:
            raise ValueError("CSV не содержит строк с данными")

        dist_m = _column(headers, data, _DIST_M_COLS)
        dist_pct = _column(headers, data, _DIST_PCT_COLS)
        speed = _detect_speed_kmh(_column(headers, data, _SPEED_COLS))
        throttle = _detect_fraction(_column(headers, data, _THROTTLE_COLS))
        brake = _detect_fraction(_column(headers, data, _BRAKE_COLS))
        steering = _detect_steering_deg(_column(headers, data, _STEER_COLS))
        gear = _column(headers, data, _GEAR_COLS)
        lap_time_col = _column(headers, data, _LAPTIME_COLS)
        session_time = _column(headers, data, _SESSIONTIME_COLS)
        lap = _column(headers, data, _LAP_COLS)

        if speed.size == 0:
            raise ValueError("в CSV не найдена колонка скорости")

        n = speed.size

        def _aligned(arr: np.ndarray) -> np.ndarray:
            if arr.size == n:
                return arr
            return np.full(n, np.nan, dtype=np.float64)

        dist_m = _aligned(dist_m)
        dist_pct = _aligned(dist_pct)
        throttle = _aligned(throttle)
        brake = _aligned(brake)
        steering = _aligned(steering)
        gear = _aligned(gear)
        lap_time_col = _aligned(lap_time_col)
        session_time = _aligned(session_time)
        lap = _aligned(lap)

        lap_length = self._infer_lap_length(dist_m, dist_pct)
        segments = self._split_laps(lap, dist_m, dist_pct)
        columns = (dist_m, dist_pct, speed, throttle, brake, steering, gear,
                   session_time, lap_time_col)
        best = self._pick_best_segment(segments, columns)
        if best is None:
            raise ValueError("не удалось извлечь ни одного круга из CSV")

        (
            dist_m_s,
            dist_pct_s,
            speed_s,
            thr_s,
            brk_s,
            steer_s,
            gear_s,
            sess_s,
            laptime_s,
        ) = best

        # Resolve the distance grid: metres when available, else fractions.
        if np.isfinite(dist_m_s).any() and np.nanmax(dist_m_s) > 1.0:
            use_pct = False
            grid = dist_m_s
        elif np.isfinite(dist_pct_s).any():
            use_pct = True
            grid = dist_pct_s
        else:
            use_pct = False
            grid = np.arange(dist_m_s.size, dtype=np.float64)

        # Require finite distance + speed; tolerate gaps in the other columns.
        keep = np.isfinite(grid) & np.isfinite(speed_s)
        if keep.sum() < 2:
            raise ValueError("в CSV слишком мало валидных точек для эталона")
        grid = grid[keep]
        speed_s = speed_s[keep]
        thr_s = thr_s[keep]
        brk_s = brk_s[keep]
        steer_s = steer_s[keep]
        gear_s = gear_s[keep]
        sess_s = sess_s[keep]
        laptime_s = laptime_s[keep]
        order = np.argsort(grid, kind="stable")
        grid = grid[order]
        speed_s = speed_s[order]
        thr_s = thr_s[order]
        brk_s = brk_s[order]
        steer_s = steer_s[order]
        gear_s = gear_s[order]
        sess_s = sess_s[order]
        laptime_s = laptime_s[order]

        # Per-point lap time relative to the start of the segment.
        lap_time_at = self._resolve_lap_time_at(sess_s, laptime_s, grid, use_pct)

        if laptime_s.size and np.isfinite(laptime_s).any():
            total = float(np.nanmax(laptime_s) - np.nanmin(laptime_s))
        elif lap_time_at.size:
            total = float(lap_time_at[-1] - lap_time_at[0])
        else:
            total = 0.0

        # Build lap_dist_pct from whatever grid we have.
        if use_pct:
            lap_dist_pct_s = grid.copy()
        elif lap_length > 0.0:
            lap_dist_pct_s = grid / lap_length
        else:
            lap_dist_pct_s = dist_pct_s[order] if dist_pct_s.size > 0 else np.zeros(0, np.float64)

        ref = ReferenceLap(
            name=os.path.basename(path),
            lap_dist=grid,
            lap_dist_pct=lap_dist_pct_s,
            speed_kmh=speed_s,
            throttle=np.nan_to_num(thr_s, nan=0.0),
            brake=np.nan_to_num(brk_s, nan=0.0),
            gear=np.nan_to_num(gear_s, nan=0.0),
            steering_deg=np.nan_to_num(steer_s, nan=0.0),
            lap_time_at=lap_time_at,
            lap_time=total,
            lap_length=lap_length,
        )
        ref._normalise_and_sort()
        if not ref.is_valid:
            raise ValueError("в CSV слишком мало валидных точек для эталона")
        self.reference = ref
        return ref

    @staticmethod
    def _infer_lap_length(dist_m: np.ndarray, dist_pct: np.ndarray) -> float:
        """Estimate track length (metres) from metre/pct distance columns."""
        if dist_m.size == 0 or dist_pct.size == 0:
            return 0.0
        mask = np.isfinite(dist_m) & np.isfinite(dist_pct) & (dist_pct > 0.05)
        if not mask.any():
            return 0.0
        ratios = dist_m[mask] / dist_pct[mask]
        ratios = ratios[np.isfinite(ratios) & (ratios > 100.0)]
        if ratios.size == 0:
            return 0.0
        return float(np.median(ratios))

    def _split_laps(self, lap: np.ndarray, dist_m: np.ndarray, dist_pct: np.ndarray):
        """Split row indices into per-lap segments (list of index arrays)."""
        n = dist_m.size
        if lap.size == n and np.isfinite(lap).any():
            ids = np.nan_to_num(lap, nan=-1.0)
            groups = []
            current = []
            last = None
            for i, lid in enumerate(ids):
                if last is None or lid == last:
                    current.append(i)
                else:
                    if len(current) > 10:
                        groups.append(np.asarray(current))
                    current = [i]
                last = lid
            if len(current) > 10:
                groups.append(np.asarray(current))
            if len(groups) > 1:
                return groups

        # Fall back to wrapping detection on the distance column.
        key = dist_pct if (np.isfinite(dist_pct).any() and np.nanmax(dist_pct) <= 1.5) else dist_m
        finite_key = key[np.isfinite(key)]
        if finite_key.size <= 1:
            return [np.arange(n)]
        span = max(1.0, float(np.nanmax(finite_key)))
        wraps = np.flatnonzero(np.diff(key) < -0.5 * (span / 20.0))
        if wraps.size >= 1:
            bounds = [0, *[int(w) + 1 for w in wraps], n]
            groups = [
                np.arange(bounds[i], bounds[i + 1])
                for i in range(len(bounds) - 1)
                if bounds[i + 1] - bounds[i] > 10
            ]
            if len(groups) > 1:
                return groups
        return [np.arange(n)]

    def _segment_duration(self, idx, session_time, lap_time_col) -> float:
        """Duration (seconds) of a lap segment, or 0 when unknown."""
        if lap_time_col.size > int(idx.max()):
            seg = lap_time_col[idx]
            if np.isfinite(seg).any():
                d = float(np.nanmax(seg) - np.nanmin(seg))
                if d > 0.0:
                    return d
        if session_time.size > int(idx.max()):
            seg = session_time[idx]
            if np.isfinite(seg).any():
                d = float(np.nanmax(seg) - np.nanmin(seg))
                if d > 0.0:
                    return d
        return 0.0

    def _pick_best_segment(self, segments, columns):
        """Choose the fastest clean lap (falls back to the last full lap)."""
        dist_m, dist_pct, speed, throttle, brake, steering, gear, session_time, lap_time_col = columns
        candidates = []
        for idx in segments:
            if idx.size < 10:
                continue
            duration = self._segment_duration(idx, session_time, lap_time_col)
            candidates.append((duration, idx))
        if not candidates:
            return None

        timed = [c for c in candidates if c[0] > 0.0]
        if timed:
            timed.sort(key=lambda c: c[0])
            chosen = timed[0][1]
        else:
            chosen = candidates[-1][1]
        return (
            dist_m[chosen],
            dist_pct[chosen],
            speed[chosen],
            throttle[chosen],
            brake[chosen],
            steering[chosen],
            gear[chosen],
            session_time[chosen],
            lap_time_col[chosen],
        )

    @staticmethod
    def _resolve_lap_time_at(sess, laptime, grid, use_pct) -> np.ndarray:
        """Per-point lap time relative to the start of the segment."""
        if laptime.size and np.isfinite(laptime).any():
            base = float(np.nanmin(laptime))
            return np.nan_to_num(laptime - base, nan=0.0)
        if sess.size and np.isfinite(sess).any():
            base = float(sess[np.flatnonzero(np.isfinite(sess))[0]])
            return np.nan_to_num(sess - base, nan=0.0)
        return np.zeros(grid.size, dtype=np.float64)

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
        lap._normalise_and_sort()
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

    def load_reference(self, filepath: str) -> ReferenceLap:
        """Load a reference lap, picking the parser by file extension.

        ``.csv``  - Garage61 / MoTeC / Z1 export (unit auto-detection);
        ``.ibt``  - native iRacing telemetry;
        ``.json`` - internal format previously saved by this app.
        """
        ext = os.path.splitext(filepath)[1].lower()
        if ext == ".csv":
            return self.load_csv(filepath)
        if ext == ".ibt":
            return self.load_ibt(filepath)
        if ext == ".json":
            return self.load_json(filepath)
        raise ValueError(f"неподдерживаемый формат файла: {ext or '(без расширения)'}")

    def describe_reference(self) -> str:
        """Human-readable summary of the active reference lap."""
        if not self.has_reference:
            return "нет"
        ref = self.reference
        parts = [ref.name, "норм. %"]
        if ref.lap_time > 0.0:
            parts.append(_format_lap_time(ref.lap_time))
        return " • ".join(parts)

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


def _format_lap_time(seconds: float) -> str:
    """Format a lap time as ``m:ss.mmm``."""
    if seconds <= 0.0:
        return "-"
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    return f"{minutes}:{rest:06.3f}"


# -- universal CSV parsing ----------------------------------------------------


def _norm(name: str) -> str:
    """Normalise a column header for fuzzy matching."""
    return "".join(ch for ch in str(name).strip().lower() if ch.isalnum() or ch == "_")


def _find_column(headers, aliases):
    """Return the index of the first header matching any alias, else None."""
    norm = [_norm(h) for h in headers]
    for alias in aliases:
        if alias in norm:
            return norm.index(alias)
    # second pass: substring match (handles prefixes like "Speed (km/h)")
    for alias in aliases:
        for i, h in enumerate(norm):
            if alias and alias in h:
                return i
    return None


def _read_csv_rows(filepath: str):
    """Read a CSV into (headers, list-of-rows), tolerating a metadata preamble.

    Garage61 (and some MoTeC / Z1 exports) print track / car / lap-time lines
    before the real header row.  We scan for the first row that looks like a
    header, i.e. one containing at least a distance and a speed column.
    """
    with open(filepath, "r", encoding="utf-8-sig", newline="") as fh:
        rows = [
            [c.strip() for c in row]
            for row in csv.reader(fh)
            if any((c or "").strip() for c in row)
        ]

    header_idx = _find_header_row(rows)
    if header_idx is None:
        raise ValueError("не удалось найти строку заголовков в CSV")
    headers = rows[header_idx]
    data = [r for r in rows[header_idx + 1:] if len(r) >= 2]
    return headers, data


def _find_header_row(rows) -> Optional[int]:
    """Locate the header row by the presence of distance + speed columns."""
    for i, row in enumerate(rows[:80]):
        norm = [_norm(c) for c in row]
        has_dist = _any_alias_match(norm, _DIST_M_COLS + _DIST_PCT_COLS)
        has_speed = _any_alias_match(norm, _SPEED_COLS)
        if has_dist and has_speed:
            return i
    return None


def _any_alias_match(norm_headers, aliases) -> bool:
    """True when any alias equals or is a substring of a normalised header."""
    for alias in aliases:
        for h in norm_headers:
            if h == alias or (alias and alias in h):
                return True
    return False


def _column(headers, data, aliases) -> np.ndarray:
    """Extract a numeric column by fuzzy alias, or an empty array."""
    idx = _find_column(headers, aliases)
    if idx is None:
        return np.zeros(0, np.float64)
    vals = []
    for row in data:
        if idx >= len(row):
            vals.append(np.nan)
            continue
        try:
            vals.append(float(row[idx]))
        except (ValueError, TypeError):
            vals.append(np.nan)
    return np.asarray(vals, dtype=np.float64)


def _detect_speed_kmh(speed: np.ndarray) -> np.ndarray:
    """Convert a speed column to km/h based on its magnitude."""
    if speed.size == 0:
        return speed
    finite = speed[np.isfinite(speed)]
    if finite.size == 0:
        return speed
    if np.nanmax(finite) < _SPEED_KMH_CUTOFF:
        return speed * _MS_TO_KMH   # looks like m/s
    return speed


def _detect_fraction(value: np.ndarray) -> np.ndarray:
    """Scale a 0-1 throttle/brake column up to 0-100 %."""
    if value.size == 0:
        return value
    finite = value[np.isfinite(value)]
    if finite.size == 0:
        return value
    if np.nanmax(finite) <= _FRACTION_CUTOFF:
        return value * 100.0
    return value


def _detect_steering_deg(angle: np.ndarray) -> np.ndarray:
    """Convert a steering column to degrees (radians when |value| is small)."""
    if angle.size == 0:
        return angle
    finite = angle[np.isfinite(angle)]
    if finite.size == 0:
        return angle
    if np.nanmax(np.abs(finite)) < _STEER_RAD_CUTOFF:
        return np.degrees(angle)
    return angle


def _clean_columns(*columns):
    """Drop rows that are non-finite in any of the primary columns."""
    arrs = [np.asarray(c, dtype=np.float64) for c in columns]
    if not arrs:
        return []
    mask = np.ones(arrs[0].size, dtype=bool)
    for a in arrs:
        if a.size == arrs[0].size:
            mask &= np.isfinite(a)
    return [a[mask] for a in arrs]