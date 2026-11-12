"""Always-on-top translucent HUD overlay for the iRacing trainer.

The window is frameless, translucent and stays above the game.  It has two
interaction modes toggled with **F8** (or the on-screen button):

* **edit**   - the window is draggable with the mouse and the buttons work.
* **locked** - mouse clicks pass straight through to iRacing
  (``WindowTransparentForInput``), so the overlay never steals input.

The window starts **unlocked** so it can be positioned immediately.  The F8
toggle and the Ctrl+F12 quit shortcut are polled globally with the Win32
``GetAsyncKeyState`` API, so they fire even while iRacing has the focus or the
overlay is transparent for input.

Live values are rendered as horizontal bars with numbers, a time-delta readout,
a brake-zone countdown plus a soft audible cue, and a set of scrolling
pyqtgraph plots comparing the driver against the reference lap.
"""

from __future__ import annotations

import ctypes
import threading
import time
from collections import deque
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import pyqtgraph as pg

from reference_manager import ReferenceManager
from telemetry_reader import TelemetryFrame, TelemetryReader

try:  # Windows-only audible cue.
    import winsound
except ImportError:  # pragma: no cover - non-Windows fallback
    winsound = None

# palette
_BG = QColor(12, 14, 18, 190)
_ACCENT = "#4fd1ff"
_PLAYER = "#3ad07a"
_REF = "#ffb347"
_BRAKE = "#ff4d4d"
_WARN = "#ffd24d"

# plot colours
_PLOT_THR_P = "#3ad07a"   # player throttle  - solid green
_PLOT_THR_R = "#8ff0b4"   # reference throttle - light green
_PLOT_BRK_P = "#ff4d4d"   # player brake - solid red
_PLOT_BRK_R = "#ff9ab5"   # reference brake - pink
_PLOT_STEER_P = "#4fd1ff"  # player steering - blue
_PLOT_STEER_R = "#ffffff"  # reference steering - white

# bar ranges
_SPEED_MAX = 320.0
_ANGLE_MAX = 270.0

# rolling history window for the plots
_HISTORY_METRES = 250.0

# audible brake cue fires when the zone is this close (seconds)
_CUE_LEAD_HI = 1.2
_CUE_LEAD_LO = 1.0

# virtual-key codes polled with GetAsyncKeyState
_VK_F8 = 0x77
_VK_F12 = 0x7B
_VK_CONTROL = 0x11


def _key_down(vk: int) -> bool:
    """True while a virtual key is held (works without window focus)."""
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:  # pragma: no cover - non-Windows
        return False


def _beep_async(freq: int = 800, duration_ms: int = 120) -> None:
    """Play a short system beep on a daemon thread (never blocks the UI)."""
    if winsound is None:
        return

    def _run() -> None:
        try:
            winsound.Beep(freq, duration_ms)
        except Exception:  # pragma: no cover - device may be busy
            pass

    threading.Thread(target=_run, daemon=True).start()


class Bar(QFrame):
    """A labelled horizontal bar with a numeric readout."""

    def __init__(self, title: str, color: str, maximum: float = 100.0) -> None:
        super().__init__()
        self._max = maximum
        self.setFixedHeight(26)
        self.setStyleSheet(
            "QFrame { background: rgba(255,255,255,12); border-radius: 4px; }"
        )

        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 6, 0)
        lay.setSpacing(6)

        self._title = QLabel(title)
        self._title.setFixedWidth(52)
        self._title.setStyleSheet("color:#9aa4b2;")

        self._track = QWidget()
        self._track.setFixedHeight(10)
        self._track.setStyleSheet(
            "background: rgba(255,255,255,25); border-radius: 5px;"
        )
        self._fill = QWidget(self._track)
        self._fill.setStyleSheet(f"background: {color}; border-radius: 5px;")
        self._fill.setGeometry(0, 0, 0, 10)

        self._value = QLabel("-")
        self._value.setFixedWidth(96)
        self._value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._value.setStyleSheet("color:#e8eef7;")

        lay.addWidget(self._title)
        lay.addWidget(self._track, 1)
        lay.addWidget(self._value)

    def set_value(self, value: float, text: Optional[str] = None) -> None:
        frac = 0.0 if self._max <= 0 else max(0.0, min(1.0, value / self._max))
        w = self._track.width()
        self._fill.setGeometry(0, 0, int(w * frac), 10)
        self._value.setText(text if text is not None else f"{value:.0f}")

    def resizeEvent(self, event):  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._fill.setGeometry(0, 0, self._fill.width(), 10)


class RollingPlot(pg.PlotWidget):
    """A pyqtgraph plot fed from a deque of ``(distance, value...)`` samples."""

    def __init__(self, maximum: float, height: int = 90) -> None:
        super().__init__()
        self._max = maximum
        self.setBackground(None)
        self.setFixedHeight(height)
        self.setMouseEnabled(x=False, y=False)
        self.hideAxis("bottom")
        self.setYRange(0.0, maximum, padding=0.0)
        self.setXRange(0.0, _HISTORY_METRES, padding=0.0)
        self.getPlotItem().showGrid(x=False, y=True, alpha=0.15)
        for axis in ("left",):
            ax = self.getAxis(axis)
            ax.setPen(pg.mkPen("#5b6675"))
            ax.setTextPen(pg.mkPen("#5b6675"))

    def set_curves(self, curves, xs, *series):
        """Push aligned x/y arrays into the pre-created curve list."""
        for curve, ys in zip(curves, series):
            curve.setData(xs, ys)


class OverlayWindow(QWidget):
    """The transparent trainer HUD."""

    def __init__(
        self,
        reader: TelemetryReader,
        references: ReferenceManager,
    ) -> None:
        super().__init__()
        self._reader = reader
        self._refs = references
        # Window starts unlocked so it can be dragged / clicked immediately.
        self._locked = False

        # rolling history (distance, throttle, brake, steering, ref variants)
        self._hist: deque = deque(maxlen=900)
        self._prev_keys = {_VK_F8: False, _VK_F12: False}
        self._cue_zone_dist: Optional[float] = None
        self._cue_active = False

        self.setWindowTitle("iRacing Coach")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(430, 560)
        self._build_ui()

        self._drag_pos = None

        self._timer = QTimer(self)
        self._timer.setInterval(1000 // 60)  # 60 Hz
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self.apply_lock(False)

    # -- UI construction ---------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        panel = QFrame(self)
        panel.setObjectName("panel")
        panel.setStyleSheet(
            "#panel { background: rgba(12,14,18,190); border-radius: 10px; }"
        )
        outer.addWidget(panel)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        # header
        header = QHBoxLayout()
        title = QLabel("iRacing Coach")
        title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color:{_ACCENT};")
        self._status = QLabel("Waiting...")
        self._status.setStyleSheet("color:#ffd24d;")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._status)
        lay.addLayout(header)

        self._ref_label = QLabel("Эталон: нет")
        self._ref_label.setStyleSheet("color:#9aa4b2;")
        lay.addWidget(self._ref_label)

        # buttons - three equal columns sized to fit the panel width
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        buttons.setContentsMargins(0, 0, 0, 0)
        self._btn_ibt = QPushButton("Загрузить .ibt")
        self._btn_ref = QPushButton("Круг → эталон")
        self._btn_lock = QPushButton("F8: клики сквозь")
        for b in (self._btn_ibt, self._btn_ref, self._btn_lock):
            b.setStyleSheet(_button_qss())
            b.setFixedHeight(26)
            b.setSizePolicy(b.sizePolicy())
            buttons.addWidget(b, 1)
        lay.addLayout(buttons)

        self._btn_ibt.clicked.connect(self._on_load_ibt)
        self._btn_ref.clicked.connect(self._on_commit_lap)
        self._btn_lock.clicked.connect(self.toggle_lock)

        # bars
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)

        self.bar_speed_p = Bar("СКОР", _PLAYER, _SPEED_MAX)
        self.bar_speed_r = Bar("СКОР✓", _REF, _SPEED_MAX)
        self.bar_thr_p = Bar("ГАЗ", _PLAYER)
        self.bar_thr_r = Bar("ГАЗ✓", _REF)
        self.bar_brk_p = Bar("ТОРМ", _BRAKE)
        self.bar_brk_r = Bar("ТОРМ✓", _REF)
        self.bar_steer = Bar("РУЛЬ", _ACCENT, _ANGLE_MAX)

        for r, (a, b) in enumerate(
            (
                (self.bar_speed_p, self.bar_speed_r),
                (self.bar_thr_p, self.bar_thr_r),
                (self.bar_brk_p, self.bar_brk_r),
            )
        ):
            grid.addWidget(a, r, 0)
            grid.addWidget(b, r, 1)
        grid.addWidget(self.bar_steer, 3, 0, 1, 2)
        lay.addLayout(grid)

        # time delta readout
        self._delta_label = QLabel("Δ: -")
        self._delta_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._delta_label.setFixedHeight(30)
        self._delta_label.setFont(QFont("Consolas", 12, QFont.Weight.Bold))
        self._delta_label.setStyleSheet(
            "background: rgba(255,255,255,12); border-radius: 6px;"
            "color:#9aa4b2; font-weight:bold;"
        )
        lay.addWidget(self._delta_label)

        # brake countdown
        self._brake_label = QLabel("Зона торможения: -")
        self._brake_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._brake_label.setFixedHeight(34)
        self._brake_label.setStyleSheet(
            "background: rgba(255,255,255,12); border-radius: 6px;"
            "color:#9aa4b2; font-weight:bold;"
        )
        lay.addWidget(self._brake_label)

        # live rolling plots
        self._thr_plot = RollingPlot(100.0, height=90)
        self._thr_plot.setYRange(0.0, 100.0, padding=0.0)
        self.curve_thr_p = self._thr_plot.plot(
            pen=pg.mkPen(_PLOT_THR_P, width=2)
        )
        self.curve_thr_r = self._thr_plot.plot(
            pen=pg.mkPen(_PLOT_THR_R, width=1, style=Qt.PenStyle.DashLine)
        )
        self.curve_brk_p = self._thr_plot.plot(
            pen=pg.mkPen(_PLOT_BRK_P, width=2)
        )
        self.curve_brk_r = self._thr_plot.plot(
            pen=pg.mkPen(_PLOT_BRK_R, width=1, style=Qt.PenStyle.DashLine)
        )
        lay.addWidget(self._thr_plot)

        self._steer_plot = RollingPlot(_ANGLE_MAX, height=70)
        self._steer_plot.setYRange(-_ANGLE_MAX, _ANGLE_MAX, padding=0.0)
        self.curve_steer_p = self._steer_plot.plot(
            pen=pg.mkPen(_PLOT_STEER_P, width=2)
        )
        self.curve_steer_r = self._steer_plot.plot(
            pen=pg.mkPen(_PLOT_STEER_R, width=1, style=Qt.PenStyle.DashLine)
        )
        lay.addWidget(self._steer_plot)

        # gear line
        self._gear_label = QLabel("Передача: -   /   эталон: -")
        self._gear_label.setStyleSheet("color:#c8d2de;")
        lay.addWidget(self._gear_label)

    # -- interaction -------------------------------------------------------

    def toggle_lock(self) -> None:
        self.apply_lock(not self._locked)

    def apply_lock(self, locked: bool) -> None:
        self._locked = locked
        # Re-applying window flags is required for the input-transparent hint.
        flags = self.windowFlags()
        if locked:
            flags |= Qt.WindowType.WindowTransparentForInput
            self._btn_lock.setText("F8: РАЗБЛОКИРОВАТЬ")
            self._btn_lock.setStyleSheet(_button_qss(active=True))
        else:
            flags &= ~Qt.WindowType.WindowTransparentForInput
            self._btn_lock.setText("F8: клики сквозь")
            self._btn_lock.setStyleSheet(_button_qss())
        self.setWindowFlags(flags)
        self.show()

    def _poll_hotkeys(self) -> None:
        """Global hotkey polling via Win32 ``GetAsyncKeyState``.

        F8 toggles the click-through lock; Ctrl+F12 quits the app.  Edge
        detection guarantees a single action per physical press even while the
        window is transparent for input or iRacing holds the focus.
        """
        f8 = _key_down(_VK_F8)
        if f8 and not self._prev_keys[_VK_F8]:
            self.toggle_lock()
        self._prev_keys[_VK_F8] = f8

        quit_combo = _key_down(_VK_F12) and _key_down(_VK_CONTROL)
        if quit_combo and not self._prev_keys[_VK_F12]:
            self.close()
        self._prev_keys[_VK_F12] = quit_combo

    def mousePressEvent(self, event):  # noqa: N802
        if not self._locked and event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._drag_pos is not None and not self._locked:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802
        self._drag_pos = None

    # -- slots -------------------------------------------------------------

    def _on_load_ibt(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите .ibt файл телеметрии", "", "iRacing telemetry (*.ibt)"
        )
        if not path:
            return
        try:
            self._refs.load_ibt(path)
            self._ref_label.setText(f"Эталон: {self._refs.reference_name}")
        except Exception as exc:
            self._ref_label.setText(f"Ошибка .ibt: {exc}")

    def _on_commit_lap(self) -> None:
        # Start recording if nothing is buffered yet.
        if not getattr(self._refs, "_recording", False):
            self._refs.start_recording()
        lap = self._refs.commit_current_lap()
        if lap is None:
            self._ref_label.setText("Эталон: буфер пуст (нужен круг)")
        else:
            self._ref_label.setText(f"Эталон: {self._refs.reference_name}")

    # -- live update -------------------------------------------------------

    def _tick(self) -> None:
        self._poll_hotkeys()

        frame = self._reader.read()

        if frame.connected and not self._refs._recording:
            self._refs.start_recording()
        if frame.connected:
            self._refs.feed(frame)

        self._update_status(frame)
        self._update_bars(frame)
        self._update_delta(frame)
        self._update_brake(frame)
        self._update_plots(frame)

    def _update_status(self, frame: TelemetryFrame) -> None:
        if frame.demo:
            self._status.setText("DEMO MODE")
            self._status.setStyleSheet(f"color:{_WARN};")
        elif frame.connected:
            self._status.setText("iRacing Connected")
            self._status.setStyleSheet(f"color:{_PLAYER};")
        else:
            self._status.setText("Waiting for iRacing...")
            self._status.setStyleSheet(f"color:{_WARN};")
        self._ref_label.setText(f"Эталон: {self._refs.reference_name}")

    def _update_bars(self, frame: TelemetryFrame) -> None:
        self.bar_speed_p.set_value(frame.speed_kmh, f"{frame.speed_kmh:.0f} км/ч")
        self.bar_thr_p.set_value(frame.throttle, f"{frame.throttle:.0f} %")
        self.bar_brk_p.set_value(frame.brake, f"{frame.brake:.0f} %")
        self.bar_steer.set_value(abs(frame.steering_deg), f"{frame.steering_deg:+.0f}°")

        ref_vals = None
        if self._refs.has_reference:
            ref_vals = self._refs.reference.sample(frame.lap_dist)

        if ref_vals:
            self.bar_speed_r.set_value(ref_vals["speed_kmh"], f"{ref_vals['speed_kmh']:.0f} км/ч")
            self.bar_thr_r.set_value(ref_vals["throttle"], f"{ref_vals['throttle']:.0f} %")
            self.bar_brk_r.set_value(ref_vals["brake"], f"{ref_vals['brake']:.0f} %")
            self._gear_label.setText(
                f"Передача: {frame.gear}   /   эталон: {ref_vals['gear']}"
            )
        else:
            for bar in (self.bar_speed_r, self.bar_thr_r, self.bar_brk_r):
                bar.set_value(0.0, "-")
            self._gear_label.setText(f"Передача: {frame.gear}   /   эталон: -")

    def _update_delta(self, frame: TelemetryFrame) -> None:
        """Colour-coded time delta against the reference lap."""
        if not self._refs.has_reference or not frame.connected:
            self._delta_label.setText("Δ: -")
            self._delta_label.setStyleSheet(
                "background: rgba(255,255,255,12); border-radius: 6px;"
                "color:#9aa4b2; font-weight:bold;"
            )
            return

        player_t = self._current_lap_time(frame)
        ref_t = self._refs.reference.time_at(frame.lap_dist)
        if ref_t <= 0.0 and player_t <= 0.0:
            self._delta_label.setText("Δ: -")
            self._delta_label.setStyleSheet(
                "background: rgba(255,255,255,12); border-radius: 6px;"
                "color:#9aa4b2; font-weight:bold;"
            )
            return

        # Positive delta => player is *ahead* (faster) than the reference.
        delta = ref_t - player_t
        if delta >= 0.0:
            color = _PLAYER
            bg = "rgba(58,208,122,40)"
        else:
            color = _BRAKE
            bg = "rgba(255,77,77,40)"
        self._delta_label.setText(f"Δ {delta:+.2f} с")
        self._delta_label.setStyleSheet(
            f"background: {bg}; border-radius: 6px;"
            f"color:{color}; font-weight:bold;"
        )

    def _current_lap_time(self, frame: TelemetryFrame) -> float:
        """Seconds elapsed on the player's current lap."""
        base = getattr(self._refs, "_lap_start_time", 0.0)
        t = frame.session_time - base
        return t if t >= 0.0 else 0.0

    def _update_brake(self, frame: TelemetryFrame) -> None:
        if not self._refs.has_reference:
            self._brake_label.setText("Зона торможения: -")
            self._brake_label.setStyleSheet(
                "background: rgba(255,255,255,12); border-radius: 6px;"
                "color:#9aa4b2; font-weight:bold;"
            )
            self._cue_zone_dist = None
            return

        zone = self._refs.reference.brake_zone_time_ahead(frame.lap_dist)
        if zone is None:
            dist = self._refs.reference.brake_zone_ahead(frame.lap_dist)
            ref_t = None
        else:
            dist, ref_t = zone

        if dist is None or dist > 150.0:
            self._brake_label.setText("Зона торможения: -")
            style_color = "#9aa4b2"
            bg = "rgba(255,255,255,12)"
            self._cue_active = False
        else:
            self._brake_label.setText(f"ТОРМОЗ ЧЕРЕЗ {dist:.0f} м")
            if dist < 50.0:
                style_color = "#ffffff"
                bg = "rgba(255,77,77,200)"
            else:
                style_color = "#1b1b1b"
                bg = "rgba(255,210,77,220)"
            self._maybe_cue_brake(frame, dist)

        self._brake_label.setStyleSheet(
            f"background: {bg}; border-radius: 6px;"
            f"color:{style_color}; font-weight:bold;"
        )

    def _maybe_cue_brake(self, frame: TelemetryFrame, dist: float) -> None:
        """Beep once per zone when it is ~1.0-1.2 s away at current speed."""
        speed_m_s = frame.speed_kmh / 3.6
        if speed_m_s < 3.0:
            return
        eta = dist / speed_m_s
        if not (_CUE_LEAD_LO <= eta <= _CUE_LEAD_HI):
            # Reset the latch once the cue window is clearly passed.
            if eta < _CUE_LEAD_LO:
                self._cue_active = False
            return
        if self._cue_active:
            return
        self._cue_active = True
        _beep_async(800, 120)

    # -- plots -------------------------------------------------------------

    def _update_plots(self, frame: TelemetryFrame) -> None:
        ref_vals = None
        if self._refs.has_reference:
            ref_vals = self._refs.reference.sample(frame.lap_dist)

        thr_r = ref_vals["throttle"] if ref_vals else 0.0
        brk_r = ref_vals["brake"] if ref_vals else 0.0
        steer_r = ref_vals["steering_deg"] if ref_vals else 0.0

        self._hist.append(
            (
                frame.lap_dist,
                frame.throttle,
                frame.brake,
                frame.steering_deg,
                thr_r,
                brk_r,
                steer_r,
            )
        )

        # Keep only the last _HISTORY_METRES of distance.
        newest = self._hist[-1][0]
        cutoff = newest - _HISTORY_METRES
        while len(self._hist) > 2 and self._hist[0][0] < cutoff:
            self._hist.popleft()

        xs = [row[0] - cutoff for row in self._hist]
        self._thr_plot.setXRange(0.0, _HISTORY_METRES, padding=0.0)
        self._thr_plot.set_curves(
            (self.curve_thr_p, self.curve_thr_r, self.curve_brk_p, self.curve_brk_r),
            xs,
            [row[1] for row in self._hist],
            [row[4] for row in self._hist],
            [row[2] for row in self._hist],
            [row[5] for row in self._hist],
        )
        self._steer_plot.setXRange(0.0, _HISTORY_METRES, padding=0.0)
        self._steer_plot.set_curves(
            (self.curve_steer_p, self.curve_steer_r),
            xs,
            [row[3] for row in self._hist],
            [row[6] for row in self._hist],
        )


def _button_qss(active: bool = False) -> str:
    if active:
        return (
            "QPushButton { background: rgba(255,210,77,150); color:#1b1b1b;"
            "border: 1px solid rgba(255,210,77,220); border-radius: 5px;"
            "padding: 3px 4px; font-size: 11px; font-weight:bold; }"
            "QPushButton:hover { background: rgba(255,210,77,200); }"
        )
    return (
        "QPushButton { background: rgba(255,255,255,20); color:#dfe7f0;"
        "border: 1px solid rgba(255,255,255,40); border-radius: 5px;"
        "padding: 3px 4px; font-size: 11px; }"
        "QPushButton:hover { background: rgba(79,209,255,60); }"
        "QPushButton:pressed { background: rgba(79,209,255,110); }"
    )