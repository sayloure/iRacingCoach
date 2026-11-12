"""Always-on-top translucent HUD overlay for the iRacing trainer.

The window is frameless, translucent and stays above the game.  It has two
interaction modes toggled with **F8** (or the on-screen button):

* **edit**  - the window is draggable with the mouse and the buttons work.
* **locked** - mouse clicks pass straight through to iRacing
  (``WindowTransparentForInput``), so the overlay never steals input.

Live values are rendered as horizontal bars with numbers, comparing the driver
against the loaded reference lap.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QKeySequence, QShortcut
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

from reference_manager import ReferenceManager
from telemetry_reader import TelemetryFrame, TelemetryReader

# palette
_BG = QColor(12, 14, 18, 190)
_ACCENT = "#4fd1ff"
_PLAYER = "#3ad07a"
_REF = "#ffb347"
_BRAKE = "#ff4d4d"
_WARN = "#ffd24d"

# bar ranges
_SPEED_MAX = 320.0
_ANGLE_MAX = 270.0


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
        self._locked = True

        self.setWindowTitle("iRacing Coach")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(430, 420)
        self._build_ui()

        self._drag_pos = None
        QShortcut(QKeySequence("F8"), self, activated=self.toggle_lock)

        self._timer = QTimer(self)
        self._timer.setInterval(1000 // 60)  # 60 Hz
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self.apply_lock(True)

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

        # buttons
        buttons = QHBoxLayout()
        self._btn_ibt = QPushButton("Загрузить .ibt")
        self._btn_ref = QPushButton("Сделать текущий круг эталоном")
        self._btn_lock = QPushButton("F8: Заблокировать окно")
        for b in (self._btn_ibt, self._btn_ref, self._btn_lock):
            b.setStyleSheet(_button_qss())
            buttons.addWidget(b)
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

        # brake countdown
        self._brake_label = QLabel("Зона торможения: -")
        self._brake_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._brake_label.setFixedHeight(34)
        self._brake_label.setStyleSheet(
            "background: rgba(255,255,255,12); border-radius: 6px;"
            "color:#9aa4b2; font-weight:bold;"
        )
        lay.addWidget(self._brake_label)

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
            self._btn_lock.setText("F8: РАЗБЛОКИРОВАТЬ (клики сквозь)")
        else:
            flags &= ~Qt.WindowType.WindowTransparentForInput
            self._btn_lock.setText("F8: ЗАБЛОКИРОВАТЬ окно")
        self.setWindowFlags(flags)
        self.show()

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

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close()

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
        frame = self._reader.read()

        if frame.connected and not self._refs._recording:
            self._refs.start_recording()
        if frame.connected:
            self._refs.feed(frame)

        self._update_status(frame)
        self._update_bars(frame)
        self._update_brake(frame)

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

    def _update_brake(self, frame: TelemetryFrame) -> None:
        if not self._refs.has_reference:
            self._brake_label.setText("Зона торможения: -")
            self._brake_label.setStyleSheet(
                "background: rgba(255,255,255,12); border-radius: 6px;"
                "color:#9aa4b2; font-weight:bold;"
            )
            return

        dist = self._refs.reference.brake_zone_ahead(frame.lap_dist)
        if dist is None or dist > 150.0:
            self._brake_label.setText("Зона торможения: -")
            style_color = "#9aa4b2"
            bg = "rgba(255,255,255,12)"
        else:
            self._brake_label.setText(f"ТОРМОЗ ЧЕРЕЗ {dist:.0f} м")
            if dist < 50.0:
                style_color = "#ffffff"
                bg = "rgba(255,77,77,200)"
            else:
                style_color = "#1b1b1b"
                bg = "rgba(255,210,77,220)"
        self._brake_label.setStyleSheet(
            f"background: {bg}; border-radius: 6px;"
            f"color:{style_color}; font-weight:bold;"
        )


def _button_qss() -> str:
    return (
        "QPushButton { background: rgba(255,255,255,20); color:#dfe7f0;"
        "border: 1px solid rgba(255,255,255,40); border-radius: 5px;"
        "padding: 4px 6px; font-size: 11px; }"
        "QPushButton:hover { background: rgba(79,209,255,60); }"
        "QPushButton:pressed { background: rgba(79,209,255,110); }"
    )