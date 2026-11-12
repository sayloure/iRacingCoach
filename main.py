import sys
import os
import re
import math
import time
import ctypes
import threading

try:
    import winsound
except ImportError:
    winsound = None

from PyQt6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QCheckBox, QFileDialog, QFrame, QSlider, QMessageBox,
    QComboBox, QColorDialog
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor

import irsdk
from config import CONFIG_FILE, load_config, save_config, vk_to_name
from state import TelemetryState
from reference import load_garage61_csv, get_target_state, rebuild_reference_time
from widgets import (
    BrakeBannerWidget,
    DeltaWidget,
    PedalsWidget,
    SteerGearRibbonWidget,
    TelemetryGraphWidget,
)


def enable_windows_1ms_timer():
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception:
        pass


def disable_windows_1ms_timer():
    try:
        ctypes.windll.winmm.timeEndPeriod(1)
    except Exception:
        pass


def get_screen_centered_geoms() -> dict:
    screen = QApplication.primaryScreen()
    if screen is not None:
        geo = screen.availableGeometry()
        sx, sy, sw, sh = geo.x(), geo.y(), geo.width(), geo.height()
    else:
        sx, sy, sw, sh = 0, 0, 1920, 1080

    banner_w, banner_h = 460, 84
    cockpit_w, cockpit_h = 620, 360
    delta_w, delta_h = 270, 175
    pedals_w, pedals_h = 240, 260
    graph_w, graph_h = 480, 240

    cx = sx + (sw - cockpit_w) // 2
    bx = sx + (sw - banner_w) // 2
    margin_x = max(40, int(sw * 0.04))

    return {
        "banner": (bx, sy + int(sh * 0.04), banner_w, banner_h, 85),
        "steer": (cx, sy + int(sh * 0.50), cockpit_w, cockpit_h, 0),
        "delta": (sx + margin_x, sy + int(sh * 0.06), delta_w, delta_h, 80),
        "pedals": (sx + margin_x, sy + sh - pedals_h - int(sh * 0.06), pedals_w, pedals_h, 80),
        "graph": (sx + sw - graph_w - margin_x, sy + sh - graph_h - int(sh * 0.06), graph_w, graph_h, 80),
    }


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


class CockpitSettingsDialog(QDialog):
    def __init__(self, parent_window):
        super().__init__(parent_window)
        self.pw = parent_window
        self.state = parent_window.state
        self.setWindowTitle("⚙ Настройка элементов, размеров и позиций виджета Кокпита")
        self.resize(620, 820)
        self.setStyleSheet(parent_window.styleSheet())

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 10, 16, 10)
        root.setSpacing(6)

        lbl_info = QLabel("Включай/выключай отдельные элементы кокпита и настраивай их прозрачность:")
        lbl_info.setStyleSheet("font-weight: bold; color: #00d2ff; font-size: 13px;")
        root.addWidget(lbl_info)

        card = QFrame()
        card.setObjectName("card")
        c_layout = QVBoxLayout(card)
        c_layout.setSpacing(5)

        gear_row = QHBoxLayout()
        lbl_g = QLabel("Отображение Передачи:")
        lbl_g.setStyleSheet("font-weight: bold; font-size: 12px;")
        self.cmb_gear = QComboBox()
        self.cmb_gear.addItem("Всегда показывать (щиток GEAR + ▲ SHIFT UP)", "always")
        self.cmb_gear.addItem("Только когда передача неверная (исчезает)", "mismatch")
        self.cmb_gear.addItem("Выключено", "off")
        self.cmb_gear.setStyleSheet(
            "QComboBox { background-color: #1a2333; border: 1px solid #2e3e58; border-radius: 5px; padding: 4px 8px; color: #ffffff; }"
        )
        self.cmb_gear.currentIndexChanged.connect(self._on_gear_mode_changed)

        lbl_gop = QLabel(f"Прозр: {self.state.cp_op_gear}%")
        lbl_gop.setFixedWidth(85)
        lbl_gop.setStyleSheet("font-size: 11px; color: #9aa5b8;")
        sld_gop = QSlider(Qt.Orientation.Horizontal)
        sld_gop.setRange(10, 100)
        sld_gop.setValue(self.state.cp_op_gear)
        sld_gop.setFixedWidth(110)
        sld_gop.valueChanged.connect(lambda val, lbl=lbl_gop: self._on_op_changed("cp_op_gear", val, lbl))

        gear_row.addWidget(lbl_g)
        gear_row.addWidget(self.cmb_gear, stretch=2)
        gear_row.addWidget(lbl_gop)
        gear_row.addWidget(sld_gop)
        c_layout.addLayout(gear_row)

        reach_row = QHBoxLayout()
        self.lbl_reach = QLabel(f"Дальность (высота) 3D-траектории сверху: {self.state.cp_ribbon_top_reach}%")
        self.lbl_reach.setStyleSheet("font-size: 12px; color: #ffd166; font-weight: bold;")
        self.sld_reach = QSlider(Qt.Orientation.Horizontal)
        self.sld_reach.setRange(25, 100)
        self.sld_reach.setValue(self.state.cp_ribbon_top_reach)
        self.sld_reach.valueChanged.connect(self._on_reach_changed)
        reach_row.addWidget(self.lbl_reach)
        reach_row.addWidget(self.sld_reach)
        c_layout.addLayout(reach_row)

        fade_row = QHBoxLayout()
        self.lbl_fade = QLabel(f"Затухание прозрачности от верха к середине: {self.state.cp_ribbon_top_fade}%")
        self.lbl_fade.setStyleSheet("font-size: 12px; color: #70e1ff; font-weight: bold;")
        self.sld_fade = QSlider(Qt.Orientation.Horizontal)
        self.sld_fade.setRange(0, 100)
        self.sld_fade.setValue(self.state.cp_ribbon_top_fade)
        self.sld_fade.valueChanged.connect(self._on_fade_changed)
        fade_row.addWidget(self.lbl_fade)
        fade_row.addWidget(self.sld_fade)
        c_layout.addLayout(fade_row)

        steer_lim_row = QHBoxLayout()
        self.lbl_steer_max = QLabel(f"Диапазон шкалы руля: ±{self.state.cp_steer_max_deg}°")
        self.lbl_steer_max.setStyleSheet("font-size: 12px; color: #55efc4; font-weight: bold;")
        self.sld_steer_max = QSlider(Qt.Orientation.Horizontal)
        self.sld_steer_max.setRange(90, 360)
        self.sld_steer_max.setValue(self.state.cp_steer_max_deg)
        self.sld_steer_max.valueChanged.connect(self._on_steer_max_changed)

        self.cb_steer_auto = QCheckBox("Авто по рефу")
        self.cb_steer_auto.setChecked(self.state.cp_steer_auto_scale)
        self.cb_steer_auto.toggled.connect(self._on_steer_auto_toggled)

        steer_lim_row.addWidget(self.lbl_steer_max)
        steer_lim_row.addWidget(self.sld_steer_max)
        steer_lim_row.addWidget(self.cb_steer_auto)
        c_layout.addLayout(steer_lim_row)

        items = [
            ("cp_show_ribbon", "cp_op_ribbon", "3D-Траектория с шевронами и степенью нажатия рефа"),
            ("cp_show_player_on_rib", "cp_op_player_on_rib", "Полоса моей позиции + мой след педалей на дорожке"),
            ("cp_show_pedal_match", None, "Бирюзовая вспышка полосы при точном попадании в педаль"),
            ("cp_show_steer", "cp_op_steer", "Тактическая дуга угла Руля"),
            ("cp_show_steer_text", None, "Текст градусов руля (STEER -18° — двигается отдельно!)"),
            ("cp_show_speed_you", "cp_op_speed", "Твоя скорость (км/ч)"),
            ("cp_show_speed_ref", None, "Скорость эталона (км/ч)"),
            ("cp_show_brake", "cp_op_brake", "Встроенная капсула Тормоза (BRAKE)"),
            ("cp_show_throttle", "cp_op_throttle", "Встроенная капсула Газа (THROTTLE)"),
            ("cp_show_clutch", "cp_op_clutch", "Встроенная капсула Сцепления (CLUTCH)"),
            ("cp_pedal_show_you_pct", None, "Показывать ТВОИ проценты (81%) рядом со шкалами педалей"),
            ("cp_pedal_show_ref_pct", None, "Показывать плашку эталона (REF: 60%) рядом со шкалами педалей"),
        ]

        self._show_cbs = {}
        self._op_controls = {"cp_op_gear": (lbl_gop, sld_gop)}

        for show_attr, op_attr, title in items:
            row = QHBoxLayout()
            cb = QCheckBox(title)
            cb.setChecked(getattr(self.state, show_attr))
            cb.toggled.connect(lambda checked, a=show_attr: self._on_show_changed(a, checked))
            row.addWidget(cb, stretch=3)
            self._show_cbs[show_attr] = cb

            if show_attr == "cp_show_steer_text":
                self.btn_steer_col = QPushButton("🎨 Цвет текста руля")
                self.btn_steer_col.setFixedWidth(145)
                self._update_color_btn_style()
                self.btn_steer_col.clicked.connect(self._pick_steer_text_color)
                row.addWidget(self.btn_steer_col)

            if op_attr is not None:
                cur_op = getattr(self.state, op_attr)
                lbl_op = QLabel(f"Прозр: {cur_op}%")
                lbl_op.setFixedWidth(85)
                lbl_op.setStyleSheet("font-size: 11px; color: #9aa5b8;")

                sld = QSlider(Qt.Orientation.Horizontal)
                sld.setRange(10, 100)
                sld.setValue(cur_op)
                sld.setFixedWidth(110)
                sld.valueChanged.connect(
                    lambda val, a=op_attr, lbl=lbl_op: self._on_op_changed(a, val, lbl)
                )
                row.addWidget(lbl_op)
                row.addWidget(sld)
                self._op_controls[op_attr] = (lbl_op, sld)

            c_layout.addLayout(row)

        root.addWidget(card)

        pos_card = QFrame()
        pos_card.setObjectName("card")
        p_layout = QVBoxLayout(pos_card)
        p_layout.setSpacing(5)
        lbl_pos = QLabel("Позиция и размер модулей (тяни мышкой за сам модуль, за его уголок ◢ или крути колёсико):")
        lbl_pos.setStyleSheet("font-weight: bold; color: #ffd166; font-size: 12px;")
        p_layout.addWidget(lbl_pos)

        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("Выбери модуль:"))
        self.cmb_mod = QComboBox()
        self.cmb_mod.addItem("🎯 Дуга Руля (Steer Arc)", "steer")
        self.cmb_mod.addItem("🔤 Текст градусов Руля (Steer Text)", "steer_text")
        self.cmb_mod.addItem("🛣 3D-Траектория (Ribbon)", "ribbon")
        self.cmb_mod.addItem("⚙ Щиток Передачи (Gear)", "gear")
        self.cmb_mod.addItem("🚀 Индикатор Скорости (Speed)", "speed")
        self.cmb_mod.addItem("🔴 Педаль Тормоза (Brake)", "brake")
        self.cmb_mod.addItem("🟢 Педаль Газа (Throttle)", "throttle")
        self.cmb_mod.addItem("🔵 Педаль Сцепления (Clutch)", "clutch")
        self.cmb_mod.setStyleSheet(
            "QComboBox { background-color: #1a2333; border: 1px solid #00d2ff; border-radius: 5px; padding: 4px 8px; color: #ffffff; font-weight: bold; }"
        )
        self.cmb_mod.currentIndexChanged.connect(self.sync_sliders_from_state)
        sel_row.addWidget(self.cmb_mod, stretch=2)
        p_layout.addLayout(sel_row)

        row_sc = QHBoxLayout()
        self.lbl_sc = QLabel("Масштаб: 100%")
        self.lbl_sc.setFixedWidth(135)
        self.sld_sc = QSlider(Qt.Orientation.Horizontal)
        self.sld_sc.setRange(40, 250)
        self.sld_sc.valueChanged.connect(self._on_sc_changed)
        row_sc.addWidget(self.lbl_sc)
        row_sc.addWidget(self.sld_sc)
        p_layout.addLayout(row_sc)

        row_x = QHBoxLayout()
        self.lbl_dx = QLabel("Смещение X: 0 px")
        self.lbl_dx.setFixedWidth(135)
        self.sld_dx = QSlider(Qt.Orientation.Horizontal)
        self.sld_dx.setRange(-400, 400)
        self.sld_dx.valueChanged.connect(self._on_dx_changed)
        row_x.addWidget(self.lbl_dx)
        row_x.addWidget(self.sld_dx)
        p_layout.addLayout(row_x)

        row_y = QHBoxLayout()
        self.lbl_dy = QLabel("Смещение Y: 0 px")
        self.lbl_dy.setFixedWidth(135)
        self.sld_dy = QSlider(Qt.Orientation.Horizontal)
        self.sld_dy.setRange(-300, 300)
        self.sld_dy.valueChanged.connect(self._on_dy_changed)
        row_y.addWidget(self.lbl_dy)
        row_y.addWidget(self.sld_dy)
        p_layout.addLayout(row_y)

        btn_row_pos = QHBoxLayout()
        btn_reset_cur = QPushButton("↺ Сбросить выбранный модуль")
        btn_reset_cur.clicked.connect(self._reset_current_mod_pos)
        btn_reset_all_mods = QPushButton("↺ Сбросить ВСЕ модули кокпита")
        btn_reset_all_mods.clicked.connect(self._reset_all_mods_pos)
        btn_row_pos.addWidget(btn_reset_cur)
        btn_row_pos.addWidget(btn_reset_all_mods)
        p_layout.addLayout(btn_row_pos)
        root.addWidget(pos_card)

        btn_close = QPushButton("✓ Готово")
        btn_close.clicked.connect(self.accept)
        root.addWidget(btn_close)

        self.sync_all_from_state()

    def _update_color_btn_style(self):
        c = self.state.cp_steer_text_color
        self.btn_steer_col.setStyleSheet(
            f"QPushButton {{ background-color: #1a2333; border: 2px solid {c}; color: {c}; padding: 3px 6px; font-size: 11px; }}"
        )

    def _pick_steer_text_color(self):
        col = QColorDialog.getColor(QColor(self.state.cp_steer_text_color), self, "Выберите цвет текста градусов руля")
        if col.isValid():
            self.state.cp_steer_text_color = col.name()
            self._update_color_btn_style()
            self.pw._save_global_opts()
            self.pw.hud_widgets["steer"].update()

    def _cur_mod_key(self) -> str:
        return self.cmb_mod.currentData() or "steer"

    def _on_gear_mode_changed(self, idx: int):
        mode = self.cmb_gear.itemData(idx)
        if mode:
            self.state.cp_gear_mode = mode
            self.pw._save_global_opts()
            self.pw.hud_widgets["steer"].update()

    def _on_reach_changed(self, val: int):
        self.state.cp_ribbon_top_reach = int(val)
        self.lbl_reach.setText(f"Дальность (высота) 3D-траектории сверху: {val}%")
        self.pw._save_global_opts()
        self.pw.hud_widgets["steer"].update()

    def _on_fade_changed(self, val: int):
        self.state.cp_ribbon_top_fade = int(val)
        self.lbl_fade.setText(f"Затухание прозрачности от верха к середине: {val}%")
        self.pw._save_global_opts()
        self.pw.hud_widgets["steer"].update()

    def _on_steer_max_changed(self, val: int):
        self.state.cp_steer_max_deg = int(val)
        self.lbl_steer_max.setText(f"Диапазон шкалы руля: ±{val}°")
        self.pw._save_global_opts()
        self.pw.hud_widgets["steer"].update()

    def _on_steer_auto_toggled(self, checked: bool):
        self.state.cp_steer_auto_scale = checked
        if checked and self.state.ref_data:
            self.pw._auto_calibrate_steer_range(self.state.ref_data)
            self.sld_steer_max.blockSignals(True)
            self.sld_steer_max.setValue(self.state.cp_steer_max_deg)
            self.lbl_steer_max.setText(f"Диапазон шкалы руля: ±{self.state.cp_steer_max_deg}°")
            self.sld_steer_max.blockSignals(False)
        self.pw._save_global_opts()
        self.pw.hud_widgets["steer"].update()

    def sync_all_from_state(self):
        self.cmb_gear.blockSignals(True)
        idx = self.cmb_gear.findData(self.state.cp_gear_mode)
        if idx >= 0:
            self.cmb_gear.setCurrentIndex(idx)
        self.cmb_gear.blockSignals(False)

        self.sld_reach.blockSignals(True)
        self.sld_reach.setValue(self.state.cp_ribbon_top_reach)
        self.lbl_reach.setText(f"Дальность (высота) 3D-траектории сверху: {self.state.cp_ribbon_top_reach}%")
        self.sld_reach.blockSignals(False)

        self.sld_fade.blockSignals(True)
        self.sld_fade.setValue(self.state.cp_ribbon_top_fade)
        self.lbl_fade.setText(f"Затухание прозрачности от верха к середине: {self.state.cp_ribbon_top_fade}%")
        self.sld_fade.blockSignals(False)

        self.sld_steer_max.blockSignals(True)
        self.sld_steer_max.setValue(self.state.cp_steer_max_deg)
        self.lbl_steer_max.setText(f"Диапазон шкалы руля: ±{self.state.cp_steer_max_deg}°")
        self.sld_steer_max.blockSignals(False)

        self.cb_steer_auto.blockSignals(True)
        self.cb_steer_auto.setChecked(self.state.cp_steer_auto_scale)
        self.cb_steer_auto.blockSignals(False)

        self._update_color_btn_style()

        for attr, cb in self._show_cbs.items():
            cb.blockSignals(True)
            cb.setChecked(getattr(self.state, attr))
            cb.blockSignals(False)
        for op_attr, (lbl, sld) in self._op_controls.items():
            val = getattr(self.state, op_attr)
            sld.blockSignals(True)
            sld.setValue(val)
            lbl.setText(f"Прозр: {val}%")
            sld.blockSignals(False)
        self.sync_sliders_from_state()

    def sync_sliders_from_state(self):
        mk = self._cur_mod_key()
        dx, dy = self.state.cp_offsets.get(mk, [0, 0])
        sc = int(self.state.cp_scales.get(mk, 100))

        self.sld_sc.blockSignals(True)
        self.sld_dx.blockSignals(True)
        self.sld_dy.blockSignals(True)

        self.sld_sc.setValue(sc)
        self.sld_dx.setValue(int(dx))
        self.sld_dy.setValue(int(dy))

        self.lbl_sc.setText(f"Масштаб: {sc}%")
        self.lbl_dx.setText(f"Смещение X: {int(dx)} px")
        self.lbl_dy.setText(f"Смещение Y: {int(dy)} px")

        self.sld_sc.blockSignals(False)
        self.sld_dx.blockSignals(False)
        self.sld_dy.blockSignals(False)

    def _on_sc_changed(self, val: int):
        mk = self._cur_mod_key()
        self.state.cp_scales[mk] = int(val)
        self.lbl_sc.setText(f"Масштаб: {val}%")
        self.pw._save_global_opts()
        self.pw.hud_widgets["steer"].update()

    def _on_dx_changed(self, val: int):
        mk = self._cur_mod_key()
        _, dy = self.state.cp_offsets.get(mk, [0, 0])
        self.state.cp_offsets[mk] = [int(val), int(dy)]
        self.lbl_dx.setText(f"Смещение X: {val} px")
        self.pw._save_global_opts()
        self.pw.hud_widgets["steer"].update()

    def _on_dy_changed(self, val: int):
        mk = self._cur_mod_key()
        dx, _ = self.state.cp_offsets.get(mk, [0, 0])
        self.state.cp_offsets[mk] = [int(dx), int(val)]
        self.lbl_dy.setText(f"Смещение Y: {val} px")
        self.pw._save_global_opts()
        self.pw.hud_widgets["steer"].update()

    def _reset_current_mod_pos(self):
        mk = self._cur_mod_key()
        self.state.cp_offsets[mk] = [0, 0]
        self.state.cp_scales[mk] = 100
        self.sync_sliders_from_state()
        self.pw._save_global_opts()
        self.pw.hud_widgets["steer"].update()

    def _reset_all_mods_pos(self):
        for k in self.state.cp_offsets:
            self.state.cp_offsets[k] = [0, 0]
            self.state.cp_scales[k] = 100
        self.sync_sliders_from_state()
        self.pw._save_global_opts()
        self.pw.hud_widgets["steer"].update()

    def _on_show_changed(self, attr: str, checked: bool):
        setattr(self.state, attr, checked)
        self.pw._save_global_opts()
        self.pw.hud_widgets["steer"].update()

    def _on_op_changed(self, attr: str, val: int, label: QLabel):
        setattr(self.state, attr, int(val))
        label.setText(f"Прозр: {val}%")
        self.pw._save_global_opts()
        self.pw.hud_widgets["steer"].update()


class ControlPanelWindow(QMainWindow):
    FPS_MODES = (30, 60)

    def __init__(self):
        super().__init__()
        enable_windows_1ms_timer()
        self.setWindowTitle("iRacing Coach — Панель управления")
        self.resize(630, 750)
        self.setStyleSheet("""
            QMainWindow, QDialog, QWidget { background-color: #10151e; color: #e4e8f0; font-family: 'Segoe UI'; }
            QPushButton {
                background-color: #1a2333; border: 1px solid #2e3e58; border-radius: 6px;
                padding: 7px 11px; font-weight: bold; color: #ffffff;
            }
            QPushButton:hover { background-color: #25334a; border-color: #00d2ff; }
            QCheckBox { font-size: 12px; spacing: 6px; padding: 2px; font-weight: bold; }
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
        self.cockpit_dlg = None

        self._prev_lap_pct = None
        self._anchor_session_time = None
        self._anchor_ref_time = 0.0
        self._track_len_checked = False
        self._last_trace_pct = None
        self._last_loaded_filename = "reference.csv"
        self._tick_counter = 0
        self._last_synced_session_time = -1.0

        self._fps_frame_count = 0
        self._fps_last_measure = time.perf_counter()

        self._apply_opts_from_cfg()

        self._lock_key_was_down = False
        self._hide_key_was_down = False
        self._last_beep_zone = -1
        self._brake_hold_active = False

        geoms = get_screen_centered_geoms()
        b_g = geoms["banner"]
        d_g = geoms["delta"]
        s_g = geoms["steer"]
        p_g = geoms["pedals"]
        g_g = geoms["graph"]

        steer_w = SteerGearRibbonWidget("steer", "Кокпит: 3D-Траектория + Руль + Педали", s_g[:4], self.state, self.layout_cfg, default_bg_pct=s_g[4])
        steer_w.on_internal_change = self._on_ribbon_moved_by_mouse

        self.hud_widgets = {
            "banner": BrakeBannerWidget("banner", "Баннер торможения", b_g[:4], self.state, self.layout_cfg, default_bg_pct=b_g[4]),
            "delta": DeltaWidget("delta", "Дельта + Круг + Позиция", d_g[:4], self.state, self.layout_cfg, default_bg_pct=d_g[4]),
            "steer": steer_w,
            "pedals": PedalsWidget("pedals", "Отдельные Педали", p_g[:4], self.state, self.layout_cfg, default_bg_pct=p_g[4]),
            "graph": TelemetryGraphWidget("graph", "График телеметрии", g_g[:4], self.state, self.layout_cfg, default_bg_pct=g_g[4]),
        }

        self.irc = irsdk.IRSDK()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.tick)

        self._build_ui()

        default_csv = os.path.join(os.path.dirname(__file__), "reference.csv")
        if os.path.exists(default_csv):
            self.load_reference_file(default_csv)

        for key, w in self.hud_widgets.items():
            enabled = self.layout_cfg.get(key, {}).get("enabled", True)
            self.checkboxes[key].setChecked(enabled)
            w.setVisible(enabled)

        self.set_fps_mode(self.state.target_fps, restart_overlays=False)

    def set_fps_mode(self, fps: int, restart_overlays: bool = True):
        fps = 30 if fps == 30 else 60
        self.state.target_fps = fps

        self.timer.stop()
        enable_windows_1ms_timer()

        if restart_overlays and not self.hud_hidden:
            for key, w in self.hud_widgets.items():
                if self.checkboxes[key].isChecked():
                    w.hide()
                    w.show()
                    w.set_locked(self.locked)
                    w.update()

        # Опрашиваем буфер каждые 2 мс: как только iRacing выложил новый 60-Гц тик (SessionTime изменился),
        # мы мгновенно рисуем кадр без биения частот и пропусков!
        self._last_synced_session_time = -1.0
        self.timer.start(2)

        self._update_fps_buttons_ui()
        self._save_global_opts()

    def _update_fps_buttons_ui(self):
        for mode, btn in self.fps_buttons.items():
            if mode == self.state.target_fps:
                btn.setStyleSheet(
                    "QPushButton { background-color: #124d5c; border: 2px solid #00f0ff; color: #ffffff; font-weight: bold; padding: 6px; }"
                )
            else:
                btn.setStyleSheet("")

    def _apply_opts_from_cfg(self):
        opts = self.layout_cfg.get("_options", {})
        saved_fps = int(opts.get("target_fps", 60))
        self.state.target_fps = 30 if saved_fps == 30 else 60
        self.state.show_clutch = bool(opts.get("show_clutch", False))
        self.state.warn_lead_sec = float(opts.get("warn_lead_sec", 5.0))
        self.state.pedals_show_you_pct = bool(opts.get("pedals_show_you_pct", True))
        self.state.pedals_show_ref_pct = bool(opts.get("pedals_show_ref_pct", True))
        self.lock_vk = int(opts.get("lock_vk", 0x77))
        self.hide_vk = int(opts.get("hide_vk", 0x78))
        self.state.cp_gear_mode = str(opts.get("cp_gear_mode", "always"))
        self.state.cp_ribbon_top_reach = int(opts.get("cp_ribbon_top_reach", 75))
        self.state.cp_ribbon_top_fade = int(opts.get("cp_ribbon_top_fade", 55))
        self.state.cp_steer_max_deg = int(opts.get("cp_steer_max_deg", 220))
        self.state.cp_steer_auto_scale = bool(opts.get("cp_steer_auto_scale", True))
        self.state.cp_steer_text_color = str(opts.get("cp_steer_text_color", "#c8e1f5"))

        default_offsets = TelemetryState().cp_offsets
        saved_offsets = opts.get("cp_offsets", {})
        for k in default_offsets:
            if k in saved_offsets and isinstance(saved_offsets[k], list) and len(saved_offsets[k]) == 2:
                self.state.cp_offsets[k] = [int(saved_offsets[k][0]), int(saved_offsets[k][1])]
            else:
                self.state.cp_offsets[k] = [0, 0]

        default_scales = TelemetryState().cp_scales
        saved_scales = opts.get("cp_scales", {})
        for k in default_scales:
            self.state.cp_scales[k] = int(saved_scales.get(k, 100))

        for attr in (
            "cp_show_ribbon", "cp_show_player_on_rib", "cp_show_pedal_match",
            "cp_show_steer", "cp_show_steer_text",
            "cp_show_speed_you", "cp_show_speed_ref",
            "cp_show_brake", "cp_show_throttle", "cp_show_clutch",
            "cp_pedal_show_you_pct", "cp_pedal_show_ref_pct",
        ):
            setattr(self.state, attr, bool(opts.get(attr, getattr(TelemetryState(), attr))))

        for op_attr in (
            "cp_op_ribbon", "cp_op_player_on_rib", "cp_op_steer",
            "cp_op_gear", "cp_op_speed", "cp_op_brake", "cp_op_throttle", "cp_op_clutch"
        ):
            setattr(self.state, op_attr, int(opts.get(op_attr, 100)))

    def _on_ribbon_moved_by_mouse(self):
        self._save_global_opts()
        if self.cockpit_dlg is not None and self.cockpit_dlg.isVisible():
            self.cockpit_dlg.sync_sliders_from_state()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(9)

        status_card = QFrame()
        status_card.setObjectName("card")
        sc_layout = QVBoxLayout(status_card)
        sc_layout.setContentsMargins(10, 8, 10, 8)
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
        opt_layout.setContentsMargins(10, 10, 10, 10)
        opt_layout.setSpacing(6)

        btn_cockpit_cfg = QPushButton("🎛 Настроить элементы, затухание, размеры и позиции Кокпита...")
        btn_cockpit_cfg.setStyleSheet(
            "QPushButton { background-color: #16364d; border: 1px solid #00d2ff; padding: 9px; font-size: 13px; }"
            "QPushButton:hover { background-color: #1f4c6b; }"
        )
        btn_cockpit_cfg.clicked.connect(self.open_cockpit_settings)
        opt_layout.addWidget(btn_cockpit_cfg)

        fps_row = QHBoxLayout()
        lbl_fps_title = QLabel("⚡ Режим частоты оверлеев:")
        lbl_fps_title.setStyleSheet("font-size: 12px; font-weight: bold; color: #55efc4;")
        fps_row.addWidget(lbl_fps_title)

        self.fps_buttons = {}
        for mode in self.FPS_MODES:
            b = QPushButton(f"{mode} FPS")
            b.clicked.connect(lambda _, m=mode: self.set_fps_mode(m, restart_overlays=True))
            fps_row.addWidget(b)
            self.fps_buttons[mode] = b
        opt_layout.addLayout(fps_row)

        self.cb_clutch = QCheckBox("Показывать Сцепление в отдельном виджете Педалей и на Графике")
        self.cb_clutch.setChecked(self.state.show_clutch)
        self.cb_clutch.toggled.connect(self._on_clutch_toggled)
        opt_layout.addWidget(self.cb_clutch)

        ped_pct_row = QHBoxLayout()
        self.cb_ped_you = QCheckBox("Отдельные Педали: показывать мои %")
        self.cb_ped_you.setChecked(self.state.pedals_show_you_pct)
        self.cb_ped_you.toggled.connect(self._on_ped_pct_toggled)
        self.cb_ped_ref = QCheckBox("Отдельные Педали: показывать плашку REF %")
        self.cb_ped_ref.setChecked(self.state.pedals_show_ref_pct)
        self.cb_ped_ref.toggled.connect(self._on_ped_pct_toggled)
        ped_pct_row.addWidget(self.cb_ped_you)
        ped_pct_row.addWidget(self.cb_ped_ref)
        opt_layout.addLayout(ped_pct_row)

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
        chk_layout.setContentsMargins(10, 8, 10, 8)
        lbl_h = QLabel("Окна оверлеев и общая прозрачность (растягивай за уголок ◢ каждого окна):")
        lbl_h.setStyleSheet("font-weight: bold; color: #00d2ff; margin-bottom: 2px;")
        chk_layout.addWidget(lbl_h)

        self.checkboxes = {}
        self._widget_sliders = {}
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
            sliders_row.setContentsMargins(22, 0, 0, 4)

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

            self._widget_sliders[key] = (lbl_bg, sld_bg, lbl_fg, sld_fg)

            sliders_row.addWidget(lbl_bg)
            sliders_row.addWidget(sld_bg)
            sliders_row.addWidget(lbl_fg)
            sliders_row.addWidget(sld_fg)
            block.addLayout(sliders_row)
            chk_layout.addLayout(block)

        root.addWidget(chk_card)

        btn_reset_all = QPushButton("↺ Сбросить все настройки и отцентрировать окна в сток")
        btn_reset_all.setStyleSheet(
            "QPushButton { background-color: #2b1820; border: 1px solid #6e2c3e; color: #ff99aa; padding: 8px; }"
            "QPushButton:hover { background-color: #42202d; border-color: #ff5566; color: #ffffff; }"
        )
        btn_reset_all.clicked.connect(self.reset_all_to_defaults)
        root.addWidget(btn_reset_all)
        root.addStretch()

    def _on_ped_pct_toggled(self):
        self.state.pedals_show_you_pct = self.cb_ped_you.isChecked()
        self.state.pedals_show_ref_pct = self.cb_ped_ref.isChecked()
        self._save_global_opts()
        self.hud_widgets["pedals"].update()

    def reset_all_to_defaults(self):
        reply = QMessageBox.question(
            self,
            "Сброс настроек",
            "Вернуть все окна оверлеев по центру текущего экрана, сбросить их размеры, прозрачность, бинды клавиш и позиции модулей кокпита в сток?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if os.path.exists(CONFIG_FILE):
            try:
                os.remove(CONFIG_FILE)
            except Exception:
                pass

        self.layout_cfg.clear()
        self._apply_opts_from_cfg()
        if self.state.ref_data and self.state.cp_steer_auto_scale:
            self._auto_calibrate_steer_range(self.state.ref_data)

        self.cb_clutch.blockSignals(True)
        self.cb_clutch.setChecked(self.state.show_clutch)
        self.cb_clutch.blockSignals(False)

        self.cb_ped_you.blockSignals(True)
        self.cb_ped_ref.blockSignals(True)
        self.cb_ped_you.setChecked(self.state.pedals_show_you_pct)
        self.cb_ped_ref.setChecked(self.state.pedals_show_ref_pct)
        self.cb_ped_you.blockSignals(False)
        self.cb_ped_ref.blockSignals(False)

        self.sld_lead.blockSignals(True)
        self.sld_lead.setValue(int(self.state.warn_lead_sec * 10))
        self.lbl_lead.setText(f"Предупреждать о тормозе заранее за: {self.state.warn_lead_sec:.1f} сек")
        self.sld_lead.blockSignals(False)

        geoms = get_screen_centered_geoms()
        for key, w in self.hud_widgets.items():
            x, y, ww, hh, bg_pct = geoms[key]
            w.setGeometry(x, y, ww, hh)
            w.bg_opacity_pct = bg_pct
            w.fg_opacity_pct = 100

            lbl_bg, sld_bg, lbl_fg, sld_fg = self._widget_sliders[key]
            sld_bg.blockSignals(True)
            sld_fg.blockSignals(True)
            sld_bg.setValue(bg_pct)
            sld_fg.setValue(100)
            lbl_bg.setText(f"Фон: {bg_pct}%")
            lbl_fg.setText("Оверлей: 100%")
            sld_bg.blockSignals(False)
            sld_fg.blockSignals(False)

            cb = self.checkboxes[key]
            cb.blockSignals(True)
            cb.setChecked(True)
            cb.blockSignals(False)
            w.setVisible(not self.hud_hidden)
            w._save_state()

        self.set_fps_mode(self.state.target_fps, restart_overlays=True)
        self._update_hotkey_labels()
        if self.cockpit_dlg is not None:
            self.cockpit_dlg.sync_all_from_state()

    def open_cockpit_settings(self):
        if self.cockpit_dlg is None:
            self.cockpit_dlg = CockpitSettingsDialog(self)
        self.cockpit_dlg.sync_all_from_state()
        self.cockpit_dlg.show()
        self.cockpit_dlg.raise_()
        self.cockpit_dlg.activateWindow()

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
            "target_fps": self.state.target_fps,
            "show_clutch": self.state.show_clutch,
            "warn_lead_sec": self.state.warn_lead_sec,
            "pedals_show_you_pct": self.state.pedals_show_you_pct,
            "pedals_show_ref_pct": self.state.pedals_show_ref_pct,
            "lock_vk": self.lock_vk,
            "hide_vk": self.hide_vk,
            "cp_offsets": self.state.cp_offsets,
            "cp_scales": self.state.cp_scales,
            "cp_gear_mode": self.state.cp_gear_mode,
            "cp_ribbon_top_reach": self.state.cp_ribbon_top_reach,
            "cp_ribbon_top_fade": self.state.cp_ribbon_top_fade,
            "cp_steer_max_deg": self.state.cp_steer_max_deg,
            "cp_steer_auto_scale": self.state.cp_steer_auto_scale,
            "cp_steer_text_color": self.state.cp_steer_text_color,
            "cp_show_ribbon": self.state.cp_show_ribbon,
            "cp_show_player_on_rib": self.state.cp_show_player_on_rib,
            "cp_show_pedal_match": self.state.cp_show_pedal_match,
            "cp_show_steer": self.state.cp_show_steer,
            "cp_show_steer_text": self.state.cp_show_steer_text,
            "cp_show_speed_you": self.state.cp_show_speed_you,
            "cp_show_speed_ref": self.state.cp_show_speed_ref,
            "cp_show_brake": self.state.cp_show_brake,
            "cp_show_throttle": self.state.cp_show_throttle,
            "cp_show_clutch": self.state.cp_show_clutch,
            "cp_pedal_show_you_pct": self.state.cp_pedal_show_you_pct,
            "cp_pedal_show_ref_pct": self.state.cp_pedal_show_ref_pct,
            "cp_op_ribbon": self.state.cp_op_ribbon,
            "cp_op_player_on_rib": self.state.cp_op_player_on_rib,
            "cp_op_steer": self.state.cp_op_steer,
            "cp_op_gear": self.state.cp_op_gear,
            "cp_op_speed": self.state.cp_op_speed,
            "cp_op_brake": self.state.cp_op_brake,
            "cp_op_throttle": self.state.cp_op_throttle,
            "cp_op_clutch": self.state.cp_op_clutch,
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

    def _auto_calibrate_steer_range(self, rows):
        if not rows:
            return
        max_rad = max(abs(r.get("SteeringWheelAngle", 0.0)) for r in rows)
        max_deg = math.degrees(max_rad)
        auto_deg = int(math.ceil((max_deg * 1.15) / 10.0) * 10)
        self.state.cp_steer_max_deg = max(200, min(360, auto_deg))

    def _update_ref_label_text(self, filename: str, num_points: int):
        mins = int(self.state.ref_lap_time // 60)
        rem_s = self.state.ref_lap_time - mins * 60
        lap_str = f"{mins}:{rem_s:06.3f}" if mins > 0 else f"{rem_s:06.3f} с"
        self.lbl_ref.setText(
            f"Эталон: {filename} ({num_points} точек, время круга: {lap_str}, руль: ±{self.state.cp_steer_max_deg}°)"
        )

    def load_reference_file(self, path: str):
        rows = load_garage61_csv(path, self.state.track_len)
        if not rows:
            self.lbl_ref.setText(f"❌ Ошибка чтения файла: {os.path.basename(path)}")
            return
        self._last_loaded_filename = os.path.basename(path)
        self.state.ref_data = rows
        self.state.ref_pcts = [r["LapDistPct"] for r in rows]

        step_g = max(1, len(rows) // 750)
        self.state.ref_graph_pts = [
            (r["LapDistPct"], r["Throttle"], r["Brake"], r.get("Clutch", 0.0))
            for r in rows[::step_g]
        ]
        self.state.ref_graph_pcts = [item[0] for item in self.state.ref_graph_pts]

        self.state.ref_lap_time = float(rows[-1]["Time"])
        self.state.brake_zones = self._detect_brake_zones(rows)
        self._anchor_session_time = None
        if self.state.cp_steer_auto_scale:
            self._auto_calibrate_steer_range(rows)
            if self.cockpit_dlg is not None:
                self.cockpit_dlg.sync_all_from_state()
        self._update_ref_label_text(self._last_loaded_filename, len(rows))

    def _sync_track_length_from_iracing(self):
        if self._track_len_checked:
            return
        self._track_len_checked = True
        try:
            wi = self.irc["WeekendInfo"]
            if isinstance(wi, dict):
                tl_str = str(wi.get("TrackLength", ""))
                m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(km|mi)", tl_str, re.IGNORECASE)
                if m:
                    val = float(m.group(1))
                    unit = m.group(2).lower()
                    real_len_m = val * 1000.0 if unit == "km" else val * 1609.34
                    if 500.0 < real_len_m < 30000.0:
                        self.state.track_len = real_len_m
                        if self.state.ref_data:
                            rebuild_reference_time(self.state.ref_data, real_len_m)
                            self.state.ref_lap_time = float(self.state.ref_data[-1]["Time"])
                            self._anchor_session_time = None
                            self._update_ref_label_text(self._last_loaded_filename, len(self.state.ref_data))
        except Exception:
            pass

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
        now_pc = time.perf_counter()

        if not self.irc.is_connected:
            if now_pc - self._fps_last_measure >= 0.05:
                self._poll_global_hotkeys()
                self.irc.startup()
                self._track_len_checked = False
                self._anchor_session_time = None
                self._last_synced_session_time = -1.0
                self._fps_last_measure = now_pc
                self.lbl_status.setText("🟡 Ожидание iRacing (зайдите в сессию)...")
                self.lbl_status.setStyleSheet("font-size: 14px; font-weight: bold; color: #ffc107;")
            return

        self.irc.freeze_var_buffer_latest()
        session_time = float(self.irc["SessionTime"] or 0.0)

        # ЖЁСТКАЯ ФАЗОВАЯ СИНХРОНИЗАЦИЯ С ДВИЖКОМ iRACING (Phase-Locked Loop):
        # Если iRacing ещё не обновил физический тик (SessionTime тот же самый) — выходим мгновенно!
        # В режиме 30 FPS ждём смены на >= 0.028 с (ровно 2 тика iRacing), в режиме 60 FPS — каждого нового тика.
        min_step = 0.028 if self.state.target_fps == 30 else 0.001
        if 0.0 <= (session_time - self._last_synced_session_time) < min_step:
            return
        self._last_synced_session_time = session_time

        self._poll_global_hotkeys()
        self._tick_counter += 1
        self._fps_frame_count += 1

        elapsed_fps = now_pc - self._fps_last_measure
        if elapsed_fps >= 0.5:
            self.state.actual_fps = self._fps_frame_count / elapsed_fps
            self._fps_frame_count = 0
            self._fps_last_measure = now_pc
            self.lbl_status.setText(
                f"🟢 iRacing SyncLocked (Трасса: {self.state.track_len:.0f} м  |  Режим: {self.state.target_fps} FPS  |  Факт: {self.state.actual_fps:.0f} FPS)"
            )
            self.lbl_status.setStyleSheet("font-size: 14px; font-weight: bold; color: #2ecc71;")

        self.state.connected = True
        self._sync_track_length_from_iracing()

        raw_lap_pct = self.irc["LapDistPct"]
        if raw_lap_pct is None:
            return
        raw_lap_pct = float(raw_lap_pct)

        speed_ms = float(self.irc["Speed"] or 0.0)
        self.state.lap_pct = raw_lap_pct
        self.state.speed_kmh = speed_ms * 3.6
        self.state.throttle = float(self.irc["Throttle"] or 0.0)
        self.state.brake = float(self.irc["Brake"] or 0.0)

        raw_cl_val = self.irc["Clutch"]
        self.state.clutch = max(0.0, min(1.0, 1.0 - float(raw_cl_val))) if raw_cl_val is not None else 0.0
        self.state.steer_rad = float(self.irc["SteeringWheelAngle"] or 0.0)
        self.state.gear = int(self.irc["Gear"] or 0)
        self.state.lap_time = float(self.irc["LapCurrentLapTime"] or 0.0)
        on_pit_road = bool(self.irc["OnPitRoad"] or False)

        if self._tick_counter % 10 == 0:
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

        if self._last_trace_pct is None or abs(raw_lap_pct - self._last_trace_pct) >= 0.0004:
            self.state.player_trace.append((raw_lap_pct, self.state.throttle, self.state.brake, self.state.clutch))
            self._last_trace_pct = raw_lap_pct

        prev_banner = self.state.banner_mode

        if self.state.ref_data:
            t = get_target_state(raw_lap_pct, self.state.ref_data, self.state.ref_pcts)
            self.state.t_speed = t["Speed"]
            self.state.t_throttle = t["Throttle"]
            self.state.t_brake = t["Brake"]
            self.state.t_clutch = t.get("Clutch", 0.0)
            self.state.t_steer_rad = t["SteeringWheelAngle"]
            self.state.t_gear = t["Gear"]
            self.state.t_time = t["Time"]
            self.state.ref_idx = t["Index"]

            self.state.delta_speed = self.state.speed_kmh - self.state.t_speed

            prev_pct = self._prev_lap_pct
            self._prev_lap_pct = raw_lap_pct

            if on_pit_road or speed_ms < 2.0:
                self._anchor_session_time = None
                self.state.delta_valid = False
            else:
                crossed_sf = (prev_pct is not None and prev_pct > 0.85 and raw_lap_pct < 0.15)
                teleported = (prev_pct is not None and abs(raw_lap_pct - prev_pct) > 0.08 and not crossed_sf)

                if crossed_sf:
                    self._anchor_session_time = session_time - (raw_lap_pct * self.state.track_len) / max(5.0, speed_ms)
                    self._anchor_ref_time = 0.0
                    self.state.delta_history.clear()
                elif self._anchor_session_time is None or teleported:
                    self._anchor_session_time = session_time
                    self._anchor_ref_time = self.state.t_time
                    self.state.delta_history.clear()

                you_elapsed = session_time - self._anchor_session_time
                ref_elapsed = self.state.t_time - self._anchor_ref_time
                self.state.delta_sec = you_elapsed - ref_elapsed
                self.state.delta_valid = True
                if self._tick_counter % 2 == 0 and abs(self.state.delta_sec) < 30.0:
                    self.state.delta_history.append(self.state.delta_sec)

            self._update_brake_coach(speed_ms)

        w_steer = self.hud_widgets["steer"]
        if w_steer.isVisible():
            w_steer.update()

        w_ped = self.hud_widgets["pedals"]
        if w_ped.isVisible():
            w_ped.update()

        is_even = (self._tick_counter % 2 == 0) or (self.state.target_fps == 30)

        w_graph = self.hud_widgets["graph"]
        if w_graph.isVisible() and is_even:
            w_graph.update()

        w_delta = self.hud_widgets["delta"]
        if w_delta.isVisible() and is_even:
            w_delta.update()

        w_banner = self.hud_widgets["banner"]
        if w_banner.isVisible() and (self.state.banner_mode or prev_banner or not self.locked) and is_even:
            w_banner.update()

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
        disable_windows_1ms_timer()
        if self.cockpit_dlg is not None:
            self.cockpit_dlg.close()
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