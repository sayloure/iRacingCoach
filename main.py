import sys
import os
import ctypes
import threading

try:
    import winsound
except ImportError:
    winsound = None

from PyQt6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QCheckBox, QFileDialog, QFrame, QSlider
)
from PyQt6.QtCore import Qt, QTimer

import irsdk
from config import load_config, save_config, vk_to_name
from state import TelemetryState
from reference import load_garage61_csv, get_target_state
from widgets import (
    BrakeBannerWidget,
    DeltaWidget,
    PedalsWidget,
    SteerGearRibbonWidget,
    TelemetryGraphWidget,
)


def qt_key_to_vk(qt_key: int, native_vk: int) -> int:
    if native_vk and native_vk > 0:
        return int(native_vk)
    if Qt.Key.Key_F1 <= qt_key <= Qt.Key.Key_F12:
        return 0x70 + (qt_key - Qt.Key.Key_F1)
    if Qt.Key.Key_A <= qt_key <= Qt.Key.Key_Z:
        return int(qt_key)
    if Qt.Key.Key_0 <= qt_key <= Qt.Key.Key_9:
        return int(qt_key)
    return 0x77


class ControlPanelWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("iRacing Coach — Панель управления")
        self.resize(610, 680)
        self.setStyleSheet("""
            QMainWindow, QWidget { background-color: #10151e; color: #e4e8f0; font-family: 'Segoe UI'; }
            QPushButton {
                background-color: #1a2333; border: 1px solid #2e3e58; border-radius: 6px;
                padding: 7px 11px; font-weight: bold; color: #ffffff;
            }
            QPushButton:hover { background-color: #25334a; border-color: #00d2ff; }
            QCheckBox { font-size: 13px; spacing: 8px; padding: 2px; font-weight: bold; }
            QFrame#card { background-color: #161d29; border: 1px solid #243044; border-radius: 8px; }
            QSlider::groove:horizontal { height: 6px; background: #243044; border-radius: 3px; }
            QSlider::sub-page:horizontal { background: #00d2ff; border-radius: 3px; }
            QSlider::handle:horizontal { width: 14px; margin: -4px 0; border-radius: 7px; background: #ffffff; }
        """)

        self.state = TelemetryState()
        self.layout_cfg = load_config()
        self.locked = False
        self.hud_hidden = False
        self._binding_target = None

        opts = self.layout_cfg.get("_options", {})
        self.state.show_clutch = bool(opts.get("show_clutch", False))
        self.state.warn_lead_sec = float(opts.get("warn_lead_sec", 5.0))
        self.lock_vk = int(opts.get("lock_vk", 0x77))  # F8
        self.hide_vk = int(opts.get("hide_vk", 0x78))  # F9

        self._lock_key_was_down = False
        self._hide_key_was_down = False
        self._last_beep_zone = -1
        self._brake_hold_active = False

        self.hud_widgets = {
            "banner": BrakeBannerWidget("banner", "Баннер торможения", (610, 40, 460, 84), self.state, self.layout_cfg, default_bg_pct=85),
            "delta": DeltaWidget("delta", "Дельта + Круг + Позиция", (60, 60, 270, 175), self.state, self.layout_cfg, default_bg_pct=80),
            "steer": SteerGearRibbonWidget("steer", "Кокпит: 3D-Траектория + Руль + Педали", (590, 540, 500, 320), self.state, self.layout_cfg, default_bg_pct=0),
            "pedals": PedalsWidget("pedals", "Отдельные Педали", (60, 660, 230, 260), self.state, self.layout_cfg, default_bg_pct=80),
            "graph": TelemetryGraphWidget("graph", "График телеметрии", (1160, 680, 480, 240), self.state, self.layout_cfg, default_bg_pct=80),
        }

        self._build_ui()

        default_csv = os.path.join(os.path.dirname(__file__), "reference.csv")
        if os.path.exists(default_csv):
            self.load_reference_file(default_csv)

        for key, w in self.hud_widgets.items():
            enabled = self.layout_cfg.get(key, {}).get("enabled", True)
            self.checkboxes[key].setChecked(enabled)
            w.setVisible(enabled)

        self.irc = irsdk.IRSDK()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        status_card = QFrame()
        status_card.setObjectName("card")
        sc_layout = QVBoxLayout(status_card)
        self.lbl_status = QLabel("🟡 Ожидание подключения к iRacing...")
        self.lbl_status.setStyleSheet("font-size: 14px; font-weight: bold; color: #ffc107;")
        self.lbl_ref = QLabel("Эталон: не загружен")
        self.lbl_ref.setStyleSheet("font-size: 12px; color: #9aa5b8;")
        sc_layout.addWidget(self.lbl_status)
        sc_layout.addWidget(self.lbl_ref)
        root.addWidget(status_card)

        btn_row = QHBoxLayout()
        self.btn_load = QPushButton("📂 Загрузить эталон (.csv)")
        self.btn_load.clicked.connect(self.open_file_dialog)
        self.btn_lock = QPushButton()
        self.btn_lock.clicked.connect(self.toggle_lock)
        self.btn_hide = QPushButton()
        self.btn_hide.clicked.connect(self.toggle_hide_all)
        btn_row.addWidget(self.btn_load)
        btn_row.addWidget(self.btn_lock)
        btn_row.addWidget(self.btn_hide)
        root.addLayout(btn_row)

        bind_row = QHBoxLayout()
        self.btn_bind_lock = QPushButton()
        self.btn_bind_lock.clicked.connect(lambda: self._start_rebinding("lock"))
        self.btn_bind_hide = QPushButton()
        self.btn_bind_hide.clicked.connect(lambda: self._start_rebinding("hide"))
        bind_row.addWidget(self.btn_bind_lock)
        bind_row.addWidget(self.btn_bind_hide)
        root.addLayout(bind_row)
        self._update_hotkey_labels()

        opt_card = QFrame()
        opt_card.setObjectName("card")
        opt_layout = QVBoxLayout(opt_card)
        lbl_opt = QLabel("Настройки телеметрии и предупреждений:")
        lbl_opt.setStyleSheet("font-weight: bold; color: #00d2ff;")
        opt_layout.addWidget(lbl_opt)

        self.cb_clutch = QCheckBox("Показывать педаль Сцепления (CLUTCH) в педалях и на графике")
        self.cb_clutch.setChecked(self.state.show_clutch)
        self.cb_clutch.toggled.connect(self._on_clutch_toggled)
        opt_layout.addWidget(self.cb_clutch)

        lead_row = QHBoxLayout()
        self.lbl_lead = QLabel(f"Предупреждать о тормозе заранее за: {self.state.warn_lead_sec:.1f} сек")
        self.lbl_lead.setStyleSheet("font-size: 12px; color: #cbd5e1;")
        self.sld_lead = QSlider(Qt.Orientation.Horizontal)
        self.sld_lead.setRange(20, 80)
        self.sld_lead.setValue(int(self.state.warn_lead_sec * 10))
        self.sld_lead.valueChanged.connect(self._on_lead_changed)
        lead_row.addWidget(self.lbl_lead)
        lead_row.addWidget(self.sld_lead)
        opt_layout.addLayout(lead_row)
        root.addWidget(opt_card)

        chk_card = QFrame()
        chk_card.setObjectName("card")
        chk_layout = QVBoxLayout(chk_card)
        lbl_h = QLabel("Виджеты и прозрачность (растягивай за уголок ◢ каждого окна):")
        lbl_h.setStyleSheet("font-weight: bold; color: #00d2ff; margin-bottom: 4px;")
        chk_layout.addWidget(lbl_h)

        self.checkboxes = {}
        titles = {
            "banner": "Баннер торможения (BRAKE IN)",
            "delta": "Виджет Дельты + Круг + Позиция",
            "steer": "Центральный кокпит (Руль + Траектория + Педали)",
            "pedals": "Отдельный блок педалей",
            "graph": "Неоновый график педалей (LIVE vs BENCHMARK)",
        }

        for key, text in titles.items():
            w = self.hud_widgets[key]
            block = QVBoxLayout()
            block.setSpacing(2)

            cb = QCheckBox(text)
            cb.setChecked(True)
            cb.toggled.connect(lambda checked, k=key: self.toggle_widget(k, checked))
            block.addWidget(cb)
            self.checkboxes[key] = cb

            sliders_row = QHBoxLayout()
            sliders_row.setContentsMargins(22, 0, 0, 5)

            lbl_bg = QLabel(f"Фон: {w.bg_opacity_pct}%")
            lbl_bg.setFixedWidth(72)
            lbl_bg.setStyleSheet("font-size: 11px; color: #9aa5b8;")
            sld_bg = QSlider(Qt.Orientation.Horizontal)
            sld_bg.setRange(0, 100)
            sld_bg.setValue(w.bg_opacity_pct)
            sld_bg.valueChanged.connect(
                lambda val, widget=w, label=lbl_bg: (label.setText(f"Фон: {val}%"), widget.set_bg_opacity(val))
            )

            lbl_fg = QLabel(f"Оверлей: {w.fg_opacity_pct}%")
            lbl_fg.setFixedWidth(92)
            lbl_fg.setStyleSheet("font-size: 11px; color: #9aa5b8; margin-left: 8px;")
            sld_fg = QSlider(Qt.Orientation.Horizontal)
            sld_fg.setRange(15, 100)
            sld_fg.setValue(w.fg_opacity_pct)
            sld_fg.valueChanged.connect(
                lambda val, widget=w, label=lbl_fg: (label.setText(f"Оверлей: {val}%"), widget.set_fg_opacity(val))
            )

            sliders_row.addWidget(lbl_bg)
            sliders_row.addWidget(sld_bg)
            sliders_row.addWidget(lbl_fg)
            sliders_row.addWidget(sld_fg)
            block.addLayout(sliders_row)
            chk_layout.addLayout(block)

        root.addWidget(chk_card)

        hint = QLabel("💡 Каждый виджет теперь лежит в своём файле в папке widgets/ — его можно менять независимо от остальных.")
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 11px; color: #7f8c9f;")
        root.addWidget(hint)
        root.addStretch()

    def _start_rebinding(self, target: str):
        self._binding_target = target
        if target == "lock":
            self.btn_bind_lock.setText("⌨ Нажмите любую клавишу... (Esc — отмена)")
            self.btn_bind_lock.setStyleSheet("background-color: #6b4c1b; border-color: #ffc107;")
        else:
            self.btn_bind_hide.setText("⌨ Нажмите любую клавишу... (Esc — отмена)")
            self.btn_bind_hide.setStyleSheet("background-color: #6b4c1b; border-color: #ffc107;")
        self.setFocus()

    def keyPressEvent(self, event):
        if self._binding_target is not None:
            if event.key() != Qt.Key.Key_Escape:
                vk = qt_key_to_vk(event.key(), event.nativeVirtualKey())
                if self._binding_target == "lock":
                    self.lock_vk = vk
                else:
                    self.hide_vk = vk
                self._save_global_opts()
            self._binding_target = None
            self.btn_bind_lock.setStyleSheet("")
            self.btn_bind_hide.setStyleSheet("")
            self._update_hotkey_labels()
            return
        super().keyPressEvent(event)

    def _update_hotkey_labels(self):
        lk = vk_to_name(self.lock_vk)
        hk = vk_to_name(self.hide_vk)
        if self.locked:
            self.btn_lock.setText(f"🔒 [{lk}]: ЗАБЛОКИРОВАНО")
            self.btn_lock.setStyleSheet("background-color: #195c36; border-color: #2ecc71;")
        else:
            self.btn_lock.setText(f"🔓 [{lk}]: Перемещение / Размер")
            self.btn_lock.setStyleSheet("")

        if self.hud_hidden:
            self.btn_hide.setText(f"👁 [{hk}]: Оверлеи СКРЫТЫ")
            self.btn_hide.setStyleSheet("background-color: #5c1926; border-color: #ff5566;")
        else:
            self.btn_hide.setText(f"👁 [{hk}]: Скрыть / Показать")
            self.btn_hide.setStyleSheet("")

        self.btn_bind_lock.setText(f"⚙ Бинд Блокировки (сейчас: {lk})")
        self.btn_bind_hide.setText(f"⚙ Бинд Скрыть/Показать (сейчас: {hk})")

    def _save_global_opts(self):
        self.layout_cfg["_options"] = {
            "show_clutch": self.state.show_clutch,
            "warn_lead_sec": self.state.warn_lead_sec,
            "lock_vk": self.lock_vk,
            "hide_vk": self.hide_vk,
        }
        save_config(self.layout_cfg)

    def _on_clutch_toggled(self, checked: bool):
        self.state.show_clutch = checked
        self._save_global_opts()
        for w in self.hud_widgets.values():
            w.update()

    def _on_lead_changed(self, val: int):
        self.state.warn_lead_sec = val / 10.0
        self.lbl_lead.setText(f"Предупреждать о тормозе заранее за: {self.state.warn_lead_sec:.1f} сек")
        self._save_global_opts()

    def toggle_widget(self, key: str, checked: bool):
        w = self.hud_widgets[key]
        if key not in self.layout_cfg:
            self.layout_cfg[key] = {}
        self.layout_cfg[key]["enabled"] = checked
        w.setVisible(checked and not self.hud_hidden)
        w._save_state()

    def toggle_lock(self):
        self.locked = not self.locked
        for key, w in self.hud_widgets.items():
            if self.checkboxes[key].isChecked() and not self.hud_hidden:
                w.set_locked(self.locked)
        self._update_hotkey_labels()

    def toggle_hide_all(self):
        self.hud_hidden = not self.hud_hidden
        for key, w in self.hud_widgets.items():
            if self.hud_hidden:
                w.hide()
            else:
                if self.checkboxes[key].isChecked():
                    w.show()
                    w.set_locked(self.locked)
        self._update_hotkey_labels()

    def open_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите файл телеметрии Garage61", os.path.dirname(__file__),
            "Телеметрия CSV (*.csv);;Все файлы (*.*)"
        )
        if path:
            self.load_reference_file(path)

    def load_reference_file(self, path: str):
        rows = load_garage61_csv(path)
        if not rows:
            self.lbl_ref.setText(f"❌ Ошибка чтения файла: {os.path.basename(path)}")
            return
        self.state.ref_data = rows
        self.state.brake_zones = self._detect_brake_zones(rows)
        lap_dur = rows[-1]["Time"]
        self.lbl_ref.setText(
            f"Эталон: {os.path.basename(path)} ({len(rows)} точек, ~{lap_dur:.1f} с, зон торможения: {len(self.state.brake_zones)})"
        )

    def _detect_brake_zones(self, rows):
        zones = []
        in_zone = False
        for i, r in enumerate(rows):
            b = r["Brake"]
            if not in_zone and b > 0.22:
                zones.append(i)
                in_zone = True
            elif in_zone and b < 0.08:
                in_zone = False
        return zones

    def _poll_global_hotkeys(self):
        if self._binding_target is not None:
            return
        try:
            u32 = ctypes.windll.user32
            lock_down = (u32.GetAsyncKeyState(self.lock_vk) & 0x8000) != 0
            if lock_down and not self._lock_key_was_down:
                self.toggle_lock()
            self._lock_key_was_down = lock_down

            hide_down = (u32.GetAsyncKeyState(self.hide_vk) & 0x8000) != 0
            if hide_down and not self._hide_key_was_down:
                self.toggle_hide_all()
            self._hide_key_was_down = hide_down
        except Exception:
            pass

    def tick(self):
        self._poll_global_hotkeys()

        if not self.irc.is_connected:
            self.irc.startup()

        self.state.connected = bool(self.irc.is_connected)
        if not self.state.connected:
            self.lbl_status.setText("🟡 Ожидание iRacing (зайдите в сессию)...")
            self.lbl_status.setStyleSheet("font-size: 14px; font-weight: bold; color: #ffc107;")
            for w in self.hud_widgets.values():
                if w.isVisible():
                    w.update()
            return

        self.lbl_status.setText("🟢 iRacing Connected (Телеметрия активна)")
        self.lbl_status.setStyleSheet("font-size: 14px; font-weight: bold; color: #2ecc71;")

        lap_pct = self.irc["LapDistPct"]
        if lap_pct is None:
            return

        speed_ms = self.irc["Speed"] or 0.0
        self.state.lap_pct = float(lap_pct)
        self.state.speed_kmh = float(speed_ms) * 3.6
        self.state.throttle = float(self.irc["Throttle"] or 0.0)
        self.state.brake = float(self.irc["Brake"] or 0.0)

        raw_clutch = self.irc["Clutch"]
        self.state.clutch = max(0.0, min(1.0, 1.0 - float(raw_clutch))) if raw_clutch is not None else 0.0

        self.state.steer_rad = float(self.irc["SteeringWheelAngle"] or 0.0)
        self.state.gear = int(self.irc["Gear"] or 0)
        self.state.lap_time = float(self.irc["LapCurrentLapTime"] or 0.0)

        self.state.current_lap = int(self.irc["Lap"] or 0)
        self.state.position = int(self.irc["PlayerCarPosition"] or 0)
        rem_raw = self.irc["SessionLapsRemainEx"]
        if rem_raw is None:
            rem_raw = self.irc["SessionLapsRemain"]
        if rem_raw is not None and 0 <= int(rem_raw) < 3000:
            self.state.laps_remain = int(rem_raw)
            self.state.total_laps = max(self.state.current_lap, self.state.current_lap + self.state.laps_remain - 1)
        else:
            self.state.laps_remain = -1
            self.state.total_laps = 0

        self.state.player_trace.append((self.state.lap_pct, self.state.throttle, self.state.brake, self.state.clutch))

        if self.state.ref_data:
            t = get_target_state(self.state.lap_pct, self.state.ref_data)
            self.state.t_speed = t["Speed"]
            self.state.t_throttle = t["Throttle"]
            self.state.t_brake = t["Brake"]
            self.state.t_clutch = t.get("Clutch", 0.0)
            self.state.t_steer_rad = t["SteeringWheelAngle"]
            self.state.t_gear = t["Gear"]
            self.state.t_time = t["Time"]
            self.state.ref_idx = t["Index"]

            self.state.delta_speed = self.state.speed_kmh - self.state.t_speed
            if self.state.lap_time > 1.0:
                self.state.delta_sec = self.state.lap_time - self.state.t_time
                if abs(self.state.delta_sec) < 25.0:
                    self.state.delta_history.append(self.state.delta_sec)

            self._update_brake_coach(speed_ms)

        for w in self.hud_widgets.values():
            if w.isVisible():
                w.update()

    def _update_brake_coach(self, speed_ms: float):
        if self.state.t_brake >= 0.12 or (self._brake_hold_active and self.state.t_brake >= 0.06):
            self._brake_hold_active = True
            self.state.banner_mode = "BRAKE_NOW"
            self.state.brake_in_sec = 0.0
            self.state.brake_dist_m = 0.0
            return
        else:
            self._brake_hold_active = False

        self.state.banner_mode = ""
        self.state.brake_in_sec = None
        self.state.brake_dist_m = None

        best_dpct = None
        best_zone_id = -1
        for z_idx in self.state.brake_zones:
            z_pct = self.state.ref_data[z_idx]["LapDistPct"]
            d = z_pct - self.state.lap_pct
            if d < 0:
                d += 1.0
            if 0.0 <= d < 0.35 and (best_dpct is None or d < best_dpct):
                best_dpct = d
                best_zone_id = z_idx

        if best_dpct is not None and speed_ms > 4.0:
            dist_m = best_dpct * self.state.track_len
            sec = dist_m / speed_ms
            lead_limit = self.state.warn_lead_sec

            if sec <= lead_limit:
                self.state.brake_in_sec = sec
                self.state.brake_dist_m = dist_m
                if sec <= 0.35:
                    self._brake_hold_active = True
                    self.state.banner_mode = "BRAKE_NOW"
                elif sec <= 2.4:
                    self.state.banner_mode = "BRAKE_SOON"
                else:
                    self.state.banner_mode = "BRAKE_PREP"

                if sec <= 1.2 and self._last_beep_zone != best_zone_id:
                    self._last_beep_zone = best_zone_id
                    if winsound:
                        threading.Thread(target=lambda: winsound.Beep(880, 95), daemon=True).start()
                return

        if self.state.t_brake < 0.05 and self.state.t_throttle > 0.90 and self.state.throttle < 0.70:
            self.state.banner_mode = "THROTTLE"

    def closeEvent(self, event):
        for w in self.hud_widgets.values():
            w.close()
        self.irc.shutdown()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    win = ControlPanelWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()