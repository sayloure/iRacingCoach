"""Telemetry reader for iRacing.

Connects to the iRacing shared memory via :class:`irsdk.IRSDK` and exposes a
single :meth:`TelemetryReader.read` call that returns a flat snapshot dict.

When iRacing is not running the reader can operate in two degraded modes:

* ``waiting``  - keep probing until the sim becomes available (default).
* ``demo``     - generate a synthetic lap so the overlay can be inspected
                 without the game (enabled with ``demo=True``).

The public API is intentionally small so higher level modules (overlay,
reference manager) do not need to know how the data is produced.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Optional

import irsdk

# iRacing reports speed in m/s, steering wheel angle in radians.
_MS_TO_KMH = 3.6
_RAD_TO_DEG = 180.0 / math.pi


@dataclass
class TelemetryFrame:
    """One telemetry sample as delivered to the rest of the app."""

    session_time: float = 0.0
    lap: int = 0
    lap_dist: float = 0.0        # metres travelled on the current lap
    lap_dist_pct: float = 0.0    # 0.0 - 1.0
    speed_kmh: float = 0.0
    throttle: float = 0.0        # 0 - 100 %
    brake: float = 0.0           # 0 - 100 %
    gear: int = 0
    steering_deg: float = 0.0
    is_on_track: bool = False
    connected: bool = False
    demo: bool = False


class TelemetryReader:
    """Thin wrapper around :class:`irsdk.IRSDK` with a demo fallback."""

    # Variables we poll from the shared memory buffer.
    VARS = (
        "SessionTime",
        "Lap",
        "LapDist",
        "LapDistPct",
        "Speed",
        "Throttle",
        "Brake",
        "Gear",
        "SteeringWheelAngle",
        "IsOnTrack",
    )

    def __init__(self, demo: bool = False) -> None:
        self._ir = irsdk.IRSDK()
        self._demo = demo
        self._demo_start = time.monotonic()
        self._demo_lap_length = 5400.0  # ~Sebring-ish, purely cosmetic
        self._connected = False
        self._last_error: Optional[str] = None

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> bool:
        """Try to attach to iRacing. Safe to call repeatedly (polling)."""
        if self._demo:
            self._connected = True
            return True
        try:
            # ``startup`` returns False when the sim is not reachable yet.
            if self._ir.startup():
                self._connected = True
                return True
        except Exception as exc:  # pragma: no cover - platform specific
            self._connected = False
            self._last_error = str(exc)
        self._connected = False
        return False

    def close(self) -> None:
        if not self._demo:
            try:
                self._ir.shutdown()
            except Exception:  # pragma: no cover
                pass
        self._connected = False

    # -- properties --------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def demo(self) -> bool:
        return self._demo

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    # -- reading -----------------------------------------------------------

    def read(self) -> TelemetryFrame:
        """Return the latest telemetry snapshot."""
        if self._demo:
            return self._read_demo()

        # Re-attach lazily if the connection dropped.
        if not self._connected:
            self.connect()
        if not self._connected:
            return TelemetryFrame(connected=False)

        try:
            self._ir.freeze_var_buffer_latest()
            ir = self._ir
            frame = TelemetryFrame(
                session_time=_f(ir["SessionTime"]),
                lap=int(_f(ir["Lap"])),
                lap_dist=_f(ir["LapDist"]),
                lap_dist_pct=_f(ir["LapDistPct"]),
                speed_kmh=_f(ir["Speed"]) * _MS_TO_KMH,
                throttle=_f(ir["Throttle"]) * 100.0,
                brake=_f(ir["Brake"]) * 100.0,
                gear=int(_f(ir["Gear"])),
                steering_deg=_f(ir["SteeringWheelAngle"]) * _RAD_TO_DEG,
                is_on_track=bool(ir["IsOnTrack"]),
                connected=True,
            )
        except Exception as exc:  # pragma: no cover - sim may be closing
            self._last_error = str(exc)
            self._connected = False
            return TelemetryFrame(connected=False)
        finally:
            try:
                self._ir.unfreeze_var_buffer_latest()
            except Exception:  # pragma: no cover
                pass
        return frame

    # -- demo --------------------------------------------------------------

    def _read_demo(self) -> TelemetryFrame:
        """Synthesise a plausible lap so the overlay works without the sim."""
        t = time.monotonic() - self._demo_start
        lap_len = self._demo_lap_length
        speed = 200.0 + 60.0 * math.sin(t * 0.7)          # km/h
        dist = (t * 45.0) % lap_len                        # steady progress
        # Braking near two "corners" of the fake lap.
        corner = min(abs((dist % (lap_len / 2.0)) - lap_len * 0.2),
                     abs((dist % (lap_len / 2.0)) - lap_len * 0.45))
        brake = max(0.0, 100.0 - corner)
        throttle = max(0.0, 100.0 - brake * 1.2)
        steering = 90.0 * math.sin(dist / 120.0)
        return TelemetryFrame(
            session_time=t,
            lap=int(t // 120) + 1,
            lap_dist=dist,
            lap_dist_pct=dist / lap_len,
            speed_kmh=max(40.0, speed - brake * 1.5),
            throttle=throttle,
            brake=brake,
            gear=max(1, min(6, int(2 + (speed - 120) / 25))),
            steering_deg=steering,
            is_on_track=True,
            connected=True,
            demo=True,
        )


def _f(value) -> float:
    """Coerce an irsdk value to float, tolerating None / lists."""
    if value is None:
        return 0.0
    if isinstance(value, (list, tuple)):
        return float(value[0]) if value else 0.0
    return float(value)