import sys
import os
import json
import math
import ctypes
import threading
from collections import deque

try:
    import winsound
except ImportError:
    winsound = None

from PyQt6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QCheckBox, QFileDialog, QFrame
)
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QBrush, QPainterPath, QPolygonF

import irsdk
from reference import load_garage61_csv, get_target_state

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "hud_layout.json")
FONT_UI = "Segoe UI"
FONT_MONO = "Consolas"


class TelemetryState:
    def __init__(self):
        self.connected = False
        self.lap_pct = 0.0
        self.speed_kmh = 0.0
        self.throttle = 0.0
        self.brake = 0.0
        self.steer_rad = 0.0
        self.gear = 0
        self.lap_time = 0.0
        self.track_len = 4500.0

        # Эталон в текущей точке
        self.t_speed = 0.0
        self.t_throttle = 0.0
        self.t_brake = 0.0
        self.t_steer_rad = 0.0
        self.t_gear = 0
        self.t_time = 0.0
        self.ref_idx = 0

        # Вычисляемые метрики тренера
        self.delta_sec = 0.0
        self.delta_speed = 0.0
        self.brake_in_sec = None
        self.brake_dist_m = None
        self.banner_mode = ""  # "BRAKE_NOW", "BRAKE_SOON", "THROTTLE", ""

        self.ref_data = []
        self.brake_zones = []  # индексы точек НАЧАЛА торможения в эталоне
        self.player_trace = deque(maxlen=250)
        self.delta_history = deque(maxlen=120)


class DraggableHUDWidget(QWidget):
    """Базовый прозрачный виджет поверх игры с поддержкой перетаскивания и F8 (клики сквозь)."""
    def __init__(self, key_name, title, default_rect, state, layout_cfg):
        super().__init__()
        self.key_name = key_name
        self.hud_title = title
        self.state = state
        self.layout_cfg = layout_cfg
        self.locked = False
        self._drag_pos = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        saved = self.layout_cfg.get(self.key_name, {})
        x = saved.get("x", default_rect[0])
        y = saved.get("y", default_rect[1])
        w = saved.get("w", default_rect[2])
        h = saved.get("h", default_rect[3])
        self.setGeometry(x, y, w, h)

    def set_locked(self, locked: bool):
        self.locked = locked
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, locked)
        self.show()
        self.update()

    def mousePressEvent(self, event):
        if not self.locked and event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if not self.locked and self._drag_pos is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            new_pos = event.globalPosition().toPoint() - self._drag_pos
            self.move(new_pos)

    def mouseReleaseEvent(self, event):
        if self._drag_pos is not None:
            self._drag_pos = None
            self.layout_cfg[self.key_name] = {
                "x": self.x(), "y": self.y(), "w": self.width(), "h": self.height(),
                "enabled": self.isVisible()
            }
            save_config(self.layout_cfg)

    def draw_card_bg(self, p: QPainter, alpha=190):
        path = QPainterPath()
        path.addRoundedRect(QRectF(1, 1, self.width() - 2, self.height() - 2), 10, 10)
        p.fillPath(path, QColor(14, 18, 24, alpha))
        if not self.locked:
            p.setPen(QPen(QColor(0, 210, 255, 180), 1.5, Qt.PenStyle.DashLine))
            p.drawPath(path)
            p.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
            p.setPen(QColor(0, 210, 255, 220))
            p.drawText(8, 14, f"✥ {self.hud_title} (тащи мышкой)")
        else:
            p.setPen(QPen(QColor(255, 255, 255, 30), 1))
            p.drawPath(path)


# ================= 1. ЦЕНТРАЛЬНЫЙ БАННЕР ТОРМОЖЕНИЯ =================
class BrakeBannerWidget(DraggableHUDWidget):
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        mode = self.state.banner_mode
        if not self.state.connected or (not mode and self.locked):
            if not self.locked:
                self.draw_card_bg(p, 150)
                p.setPen(QColor(180, 190, 200))
                p.setFont(QFont(FONT_UI, 12, QFont.Weight.Bold))
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "BRAKE BANNER (в ожидании зоны торможения)")
            return

        path = QPainterPath()
        path.addRoundedRect(QRectF(2, 2, w - 4, h - 4), 10, 10)

        if mode == "BRAKE_NOW":
            bg = QColor(200, 20, 30, 235)
            border = QColor(255, 90, 90)
            title = "[!]   ТОРМОЗИ!   [!]"
            ref_b = self.state.t_brake * 100.0
            you_b = self.state.brake * 100.0
            sub = f"ЦЕЛЬ ТОРМОЗА: {ref_b:.0f}%   (ТЫ: {you_b:.0f}%)"
            pct = max(0.05, min(1.0, self.state.t_brake))
        elif mode == "BRAKE_SOON":
            sec = self.state.brake_in_sec or 0.0
            dist = self.state.brake_dist_m or 0.0
            bg = QColor(45, 22, 12, 220)
            border = QColor(255, 150, 40)
            title = f"ТОРМОЗ ЧЕРЕЗ {sec:.1f} с"
            sub = f"ДИСТАНЦИЯ: {dist:.0f} м"
            pct = max(0.0, min(1.0, 1.0 - (sec / 3.2)))
        elif mode == "THROTTLE":
            bg = QColor(18, 95, 45, 205)
            border = QColor(60, 255, 120)
            title = "ПОЛНЫЙ ГАЗ"
            sub = "ВЫХОД ИЗ ПОВОРОТА"
            pct = 1.0
        else:
            self.draw_card_bg(p, 140)
            return

        p.fillPath(path, bg)
        p.setPen(QPen(border, 2.2))
        p.drawPath(path)

        # Главный текст
        p.setPen(Qt.GlobalColor.white)
        p.setFont(QFont(FONT_UI, 18, QFont.Weight.Bold))
        p.drawText(0, 5, w, 34, Qt.AlignmentFlag.AlignCenter, title)

        # Полоса прогресса / силы торможения
        bar_w = w - 56
        bar_x = 28
        bar_y = 43
        p.fillRect(bar_x, bar_y, bar_w, 11, QColor(0, 0, 0, 150))
        fill_w = int(bar_w * pct)
        bar_col = QColor(255, 220, 60) if mode == "BRAKE_NOW" else QColor(255, 165, 40) if mode == "BRAKE_SOON" else QColor(60, 255, 120)
        p.fillRect(bar_x, bar_y, fill_w, 11, bar_col)

        # В режиме торможения рисуем отметку твоего нажатия поверх целевой полосы
        if mode == "BRAKE_NOW":
            you_x = bar_x + int(bar_w * max(0.0, min(1.0, self.state.brake)))
            p.setPen(QPen(Qt.GlobalColor.white, 3))
            p.drawLine(you_x, bar_y - 2, you_x, bar_y + 13)

        # Подпись дистанции / процентов
        p.setFont(QFont(FONT_MONO, 10, QFont.Weight.Bold))
        p.setPen(QColor(240, 240, 245))
        p.drawText(0, 56, w, 22, Qt.AlignmentFlag.AlignCenter, sub)


# ================= 2. ВИДЖЕТ ДЕЛЬТЫ ВРЕМЕНИ =================
class DeltaWidget(DraggableHUDWidget):
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.draw_card_bg(p, 200)
        w, h = self.width(), self.height()

        p.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
        p.setPen(QColor(150, 165, 180))
        p.drawText(12, 8, w - 24, 18, Qt.AlignmentFlag.AlignLeft, "DELTA TIME vs REF")

        d = self.state.delta_sec
        if abs(d) > 25.0 or not self.state.connected:
            txt = "Δ --.-- s"
            col = QColor(160, 165, 175)
        else:
            txt = f"{d:+.2f}s"
            col = QColor(50, 255, 110) if d <= 0 else QColor(255, 70, 70)

        p.setFont(QFont(FONT_MONO, 26, QFont.Weight.Bold))
        p.setPen(col)
        p.drawText(0, 22, w, 44, Qt.AlignmentFlag.AlignCenter, txt)

        ds = self.state.delta_speed
        p.setFont(QFont(FONT_MONO, 10, QFont.Weight.Bold))
        p.setPen(QColor(200, 210, 220))
        p.drawText(0, 64, w, 18, Qt.AlignmentFlag.AlignCenter, f"Скорость: {ds:+.0f} км/ч")

        gx, gy, gw, gh = 12, 86, w - 24, h - 96
        if gh > 15:
            p.fillRect(gx, gy, gw, gh, QColor(8, 10, 14, 150))
            mid_y = gy + gh // 2
            p.setPen(QPen(QColor(255, 255, 255, 40), 1, Qt.PenStyle.DashLine))
            p.drawLine(gx, mid_y, gx + gw, mid_y)

            hist = list(self.state.delta_history)
            if len(hist) > 2:
                p.setPen(QPen(col, 2))
                for i in range(len(hist) - 1):
                    x1 = gx + int(i / max(1, len(hist) - 1) * gw)
                    x2 = gx + int((i + 1) / max(1, len(hist) - 1) * gw)
                    v1 = max(-2.0, min(2.0, hist[i]))
                    v2 = max(-2.0, min(2.0, hist[i + 1]))
                    y1 = int(mid_y - (v1 / 2.0) * (gh * 0.42))
                    y2 = int(mid_y - (v2 / 2.0) * (gh * 0.42))
                    p.drawLine(x1, y1, x2, y2)


# ================= 3. ОТДЕЛЬНЫЙ ВИДЖЕТ ПЕДАЛЕЙ (СОВМЕЩЁННЫЕ ШКАЛЫ) =================
class PedalsWidget(DraggableHUDWidget):
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.draw_card_bg(p, 200)
        w, h = self.width(), self.height()

        p.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
        p.setPen(QColor(160, 175, 190))
        p.drawText(10, 6, w - 20, 18, Qt.AlignmentFlag.AlignCenter, "PEDALS: YOU vs REF")

        top_y = 48
        bot_y = h - 24
        bar_h = max(20, bot_y - top_y)

        draw_unified_pedal_bar(
            p, x_center=w // 4 + 4, top_y=top_y, bar_w=38, bar_h=bar_h,
            you_val=self.state.brake, ref_val=self.state.t_brake,
            label="BRAKE", main_col=QColor(255, 50, 60), ref_col=QColor(255, 160, 40)
        )
        draw_unified_pedal_bar(
            p, x_center=(w * 3) // 4 - 4, top_y=top_y, bar_w=38, bar_h=bar_h,
            you_val=self.state.throttle, ref_val=self.state.t_throttle,
            label="THROTTLE", main_col=QColor(45, 245, 95), ref_col=QColor(180, 255, 200)
        )


def draw_unified_pedal_bar(p: QPainter, x_center: int, top_y: int, bar_w: int, bar_h: int,
                           you_val: float, ref_val: float, label: str, main_col: QColor, ref_col: QColor):
    """
    Рисует объединённый столбик педали:
    - полупрозрачная заливка — целевой уровень ЭТАЛОНА (REF) + яркая горизонтальная планка-мишень;
    - внутренняя яркая колонка — нажатие ИГРОКА (YOU).
    """
    x0 = x_center - bar_w // 2
    you_v = max(0.0, min(1.0, you_val))
    ref_v = max(0.0, min(1.0, ref_val))

    # Цифры YOU / REF сверху
    p.setFont(QFont(FONT_MONO, 9, QFont.Weight.Bold))
    p.setPen(Qt.GlobalColor.white)
    p.drawText(x_center - 48, top_y - 20, 96, 16, Qt.AlignmentFlag.AlignCenter,
               f"{you_v * 100:.0f}% / {ref_v * 100:.0f}%")

    # Фон стакана
    p.fillRect(x0, top_y, bar_w, bar_h, QColor(18, 22, 30, 220))

    # 1. Заливка ЭТАЛОНА (на всю ширину стакана, полупрозрачная)
    rh = int(bar_h * ref_v)
    ref_fill = QColor(main_col.red(), main_col.green(), main_col.blue(), 65)
    p.fillRect(x0 + 1, top_y + bar_h - rh, bar_w - 2, rh, ref_fill)

    # 2. Заливка ИГРОКА (чуть уже стакана, яркая)
    yh = int(bar_h * you_v)
    pad = 5
    p.fillRect(x0 + pad, top_y + bar_h - yh, bar_w - pad * 2, yh, main_col)

    # 3. Яркая горизонтальная ПЛАНКА-МИШЕНЬ эталона (куда нужно попасть!)
    if ref_v > 0.02:
        ry = top_y + bar_h - rh
        p.setPen(QPen(Qt.GlobalColor.white, 3))
        p.drawLine(x0 - 3, ry, x0 + bar_w + 3, ry)
        p.setPen(QPen(ref_col, 1.5))
        p.drawLine(x0 - 3, ry - 2, x0 + bar_w + 3, ry - 2)

    # Контур стакана (светится зелёным/голубым, если игрок точно попал в эталон)
    diff = abs(you_v - ref_v)
    border_col = QColor(0, 240, 180, 220) if (ref_v > 0.1 and diff < 0.08) else QColor(90, 105, 125, 180)
    p.setPen(QPen(border_col, 1.5))
    p.drawRect(x0, top_y, bar_w, bar_h)

    # Подпись снизу
    p.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
    p.setPen(QColor(195, 205, 220))
    p.drawText(x_center - 45, top_y + bar_h + 4, 90, 16, Qt.AlignmentFlag.AlignCenter, label)


# ================= 4. ГЛАВНЫЙ КОКПИТ-ВИДЖЕТ: 3D-ТРАЕКТОРИЯ + РУЛЬ + ПЕДАЛИ + ПЕРЕДАЧА =================
class SteerGearRibbonWidget(DraggableHUDWidget):
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.draw_card_bg(p, 175)
        w, h = self.width(), self.height()

        # Встроенные совмещённые столбики ТОРМОЗА (слева) и ГАЗА (справа) прямо внутри виджета!
        pedal_top = 42
        pedal_h = h - 68
        draw_unified_pedal_bar(
            p, x_center=38, top_y=pedal_top, bar_w=30, bar_h=pedal_h,
            you_val=self.state.brake, ref_val=self.state.t_brake,
            label="ТОРМОЗ", main_col=QColor(255, 50, 60), ref_col=QColor(255, 200, 50)
        )
        draw_unified_pedal_bar(
            p, x_center=w - 38, top_y=pedal_top, bar_w=30, bar_h=pedal_h,
            you_val=self.state.throttle, ref_val=self.state.t_throttle,
            label="ГАЗ", main_col=QColor(45, 245, 95), ref_col=QColor(180, 255, 200)
        )

        # Центральная зона для 3D-дорожки и руля (между двумя педалями)
        center_left = 76
        center_w = w - 152

        # 1. Рисуем 3D-ленту траектории впереди с шевронами дистанции и маркерами передач
        self._draw_trajectory_ribbon(p, center_left, center_w, int(h * 0.54))

        # 2. Дуга угла руля (ACTUAL vs BENCHMARK)
        arc_w = min(230, center_w - 16)
        arc_h = 88
        arc_x = center_left + (center_w - arc_w) // 2
        arc_y = int(h * 0.50)

        rect_outer = QRectF(arc_x, arc_y, arc_w, arc_h * 2)
        rect_inner = QRectF(arc_x + 10, arc_y + 10, arc_w - 20, (arc_h - 10) * 2)

        p.setPen(QPen(QColor(70, 80, 95, 180), 6))
        p.drawArc(rect_outer, 0 * 16, 180 * 16)
        p.setPen(QPen(QColor(55, 65, 75, 160), 4))
        p.drawArc(rect_inner, 0 * 16, 180 * 16)

        # ВАЖНО: В iRacing SteeringWheelAngle > 0 — это поворот ВЛЕВО, а < 0 — ВПРАВО.
        # Инвертируем знак (-math.degrees), чтобы поворот руля вправо давал "+" и двигал ползунок ВПРАВО!
        you_deg = -math.degrees(self.state.steer_rad)
        ref_deg = -math.degrees(self.state.t_steer_rad)

        def deg_to_qt_angle(deg_right_positive):
            clamped = max(-180.0, min(180.0, deg_right_positive))
            # В Qt 90° = 12 часов (верх), 0° = 3 часа (право), 180° = 9 часов (лево).
            # Поэтому положительный угол (вправо) уменьшает угол от 90° к 0°:
            return int((90.0 - clamped * 0.45) * 16)

        # Центральная засечка "0 градусов" на 12 часах
        p.setPen(QPen(QColor(255, 255, 255, 120), 2))
        cx_arc = arc_x + arc_w // 2
        p.drawLine(cx_arc, arc_y - 4, cx_arc, arc_y + 14)

        # Метка REF (внутренняя оранжевая дуга)
        ref_ang = deg_to_qt_angle(ref_deg)
        p.setPen(QPen(QColor(255, 165, 45), 6))
        p.drawArc(rect_inner, ref_ang - 6 * 16, 12 * 16)

        # Метка YOU (внешняя голубая дуга)
        you_ang = deg_to_qt_angle(you_deg)
        diff = abs(you_deg - ref_deg)
        you_col = QColor(40, 225, 255) if diff < 12 else QColor(255, 75, 75)
        p.setPen(QPen(you_col, 7))
        p.drawArc(rect_outer, you_ang - 6 * 16, 12 * 16)

        # Подписи градусов руля (ACTUAL слева, REF справа)
        p.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        p.setPen(you_col)
        p.drawText(center_left + 4, arc_y + 2, 85, 14, Qt.AlignmentFlag.AlignLeft, "ТЫ (РУЛЬ)")
        p.setFont(QFont(FONT_MONO, 13, QFont.Weight.Bold))
        p.drawText(center_left + 4, arc_y + 16, 85, 22, Qt.AlignmentFlag.AlignLeft, f"{you_deg:+.0f}°")

        p.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        p.setPen(QColor(255, 165, 45))
        p.drawText(center_left + center_w - 89, arc_y + 2, 85, 14, Qt.AlignmentFlag.AlignRight, "ЭТАЛОН")
        p.setFont(QFont(FONT_MONO, 13, QFont.Weight.Bold))
        p.drawText(center_left + center_w - 89, arc_y + 16, 85, 22, Qt.AlignmentFlag.AlignRight, f"{ref_deg:+.0f}°")

        # 3. Блок GEAR COACH и СКОРОСТИ под дугой
        gear_y = h - 66
        g_you = self.state.gear
        g_ref = self.state.t_gear
        g_you_str = "R" if g_you == -1 else "N" if g_you == 0 else str(g_you)
        g_ref_str = str(g_ref) if g_ref > 0 else "-"

        if g_ref > 0 and g_you != g_ref:
            arrow = "↓" if g_ref < g_you else "↑"
            g_text = f"{g_you_str} → {g_ref_str} {arrow}"
            g_col = QColor(255, 110, 45) if g_ref < g_you else QColor(80, 255, 120)
        else:
            g_text = f"GEAR {g_you_str}"
            g_col = Qt.GlobalColor.white

        p.setFont(QFont(FONT_MONO, 20, QFont.Weight.Bold))
        p.setPen(g_col)
        p.drawText(center_left, gear_y, center_w, 28, Qt.AlignmentFlag.AlignCenter, g_text)

        # Скорость и дельта скорости в точке
        spd_col = QColor(60, 255, 120) if self.state.delta_speed >= -2 else QColor(255, 180, 60)
        p.setFont(QFont(FONT_MONO, 12, QFont.Weight.Bold))
        p.setPen(spd_col)
        p.drawText(center_left, gear_y + 29, center_w, 22, Qt.AlignmentFlag.AlignCenter,
                   f"{self.state.speed_kmh:.0f} км/ч  (эталон: {self.state.t_speed:.0f})")

    def _draw_trajectory_ribbon(self, p: QPainter, x_offset: int, w_zone: int, h_zone: int):
        """
        Уходящая в перспективу 3D-дорожка эталона на следующие ~160 метров:
        - изгибается ВПРАВО, когда руль эталона повёрнут вправо, и ВЛЕВО — когда влево;
        - ширина центральной заливки пропорциональна нажатию педали эталона;
        - рисует шевроны дистанции (50м, 100м, 150м) и точки переключения передач на траектории.
        """
        ref = self.state.ref_data
        if not ref:
            return

        n = len(ref)
        idx0 = self.state.ref_idx
        steps = 22
        # Берём ~160 метров вперёд по трассе
        lookahead_pct = min(0.06, 160.0 / max(1000.0, self.state.track_len))
        step_stride = max(1, int((n * lookahead_pct) / steps))

        cx = x_offset + w_zone * 0.5
        bottom_y = h_zone - 2
        top_y = 14

        curve_x = 0.0
        slope = 0.0
        pts_left = []
        pts_right = []
        pts_center = []
        seg_data = []

        prev_gear = ref[idx0]["Gear"]

        for s in range(steps):
            pt = ref[(idx0 + s * step_stride) % n]
            t = s / float(steps - 1)
            # Перспективное сжатие по высоте (ближе к нам шаги крупнее, вдали — плотнее)
            persp_t = math.pow(t, 0.82)
            y = bottom_y - persp_t * (bottom_y - top_y)

            # ИСПРАВЛЕНО: В iRacing отрицательный угол руля = поворот ВПРАВО -> смещаем дорожку ВПРАВО (+x)
            steer_right_deg = -math.degrees(pt["SteeringWheelAngle"])
            slope += (steer_right_deg / 90.0) * 3.8
            curve_x += slope * (0.35 + 0.65 * t)
            # Ограничиваем вылет дорожки за края зоны
            max_offset = w_zone * 0.36
            curve_x = max(-max_offset, min(max_offset, curve_x))

            half_w = (1.0 - t * 0.74) * 52.0
            pts_left.append(QPointF(cx + curve_x - half_w, y))
            pts_right.append(QPointF(cx + curve_x + half_w, y))
            pts_center.append(QPointF(cx + curve_x, y))

            brk = pt["Brake"]
            thr = pt["Throttle"]
            gear = pt.get("Gear", 0)
            gear_change = gear if (gear > 0 and prev_gear > 0 and gear != prev_gear) else 0
            prev_gear = gear

            seg_data.append((brk, thr, t, half_w, gear_change))

        # Отрисовка сегментов дорожки от ближних к дальним
        for s in range(steps - 1):
            brk, thr, t, half_w, _ = seg_data[s]
            alpha = int(215 * (1.0 - t * 0.45))

            # Базовое полотно асфальта
            poly_road = QPolygonF([pts_left[s], pts_right[s], pts_right[s + 1], pts_left[s + 1]])
            p.setBrush(QBrush(QColor(25, 32, 42, int(alpha * 0.7))))
            p.setPen(QPen(QColor(120, 140, 165, int(alpha * 0.6)), 1))
            p.drawPolygon(poly_road)

            # Активная цветовая полоса внутри дорожки (ширина показывает силу нажатия тормоза/газа!)
            if brk > 0.08:
                col = QColor(255, 45, 55, alpha) if brk > 0.35 else QColor(255, 165, 30, alpha)
                intensity = max(0.25, min(1.0, brk))
            elif thr > 0.15:
                col = QColor(40, 245, 95, int(alpha * 0.88))
                intensity = max(0.25, min(1.0, thr))
            else:
                col = QColor(0, 195, 255, int(alpha * 0.65))
                intensity = 0.35

            w0 = half_w * intensity
            w1 = seg_data[s + 1][3] * intensity
            c0, c1 = pts_center[s], pts_center[s + 1]
            poly_active = QPolygonF([
                QPointF(c0.x() - w0, c0.y()),
                QPointF(c0.x() + w0, c0.y()),
                QPointF(c1.x() + w1, c1.y()),
                QPointF(c1.x() - w1, c1.y()),
            ])
            p.setBrush(QBrush(col))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(poly_active)

            # Дистанционные метки 50м / 100м и маркеры переключения передач на траектории
            if s in (6, 13):
                dist_label = "50m" if s == 6 else "100m"
                p.setPen(QPen(QColor(255, 255, 255, 130), 1, Qt.PenStyle.DashLine))
                p.drawLine(pts_left[s], pts_right[s])
                p.setFont(QFont(FONT_MONO, 7, QFont.Weight.Bold))
                p.setPen(QColor(210, 220, 235, 180))
                p.drawText(int(pts_right[s].x() + 4), int(pts_right[s].y() + 3), dist_label)

        # Рисуем значки переключения передачи прямо поверх 3D-дорожки в месте переключения
        for s in range(1, steps - 1):
            gear_change = seg_data[s][4]
            if gear_change > 0:
                cpt = pts_center[s]
                p.setFont(QFont(FONT_MONO, 9, QFont.Weight.Bold))
                p.setPen(QColor(255, 230, 80))
                p.drawText(int(cpt.x() - 28), int(cpt.y() - 8), 56, 16,
                           Qt.AlignmentFlag.AlignCenter, f"GEAR {gear_change}")
                break  # Показываем только ближайшее переключение


# ================= 5. ЖИВОЙ ГРАФИК ПО ДИСТАНЦИИ =================
class TelemetryGraphWidget(DraggableHUDWidget):
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.draw_card_bg(p, 205)
        w, h = self.width(), self.height()

        p.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
        p.setPen(QColor(160, 175, 195))
        p.drawText(12, 6, w - 24, 18, Qt.AlignmentFlag.AlignLeft,
                   f"LIVE vs BENCHMARK   |   {self.state.speed_kmh:.0f} км/ч")

        gx, gy = 10, 26
        gw, gh = w - 20, h - 34
        p.fillRect(gx, gy, gw, gh, QColor(8, 11, 16, 190))

        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        for k in (0.25, 0.5, 0.75):
            yy = int(gy + gh * k)
            p.drawLine(gx, yy, gx + gw, yy)

        now_x = gx + int(gw * 0.38)
        p.setPen(QPen(QColor(255, 255, 255, 110), 1.5, Qt.PenStyle.DashLine))
        p.drawLine(now_x, gy, now_x, gy + gh)

        cx = self.state.lap_pct
        win_back = 0.08
        win_ahead = 0.13
        total_span = win_back + win_ahead

        def rel_pct(pt_pct):
            d = pt_pct - cx
            if d > 0.5:
                d -= 1.0
            elif d < -0.5:
                d += 1.0
            return d

        def to_xy(rel_d, val_0_1):
            x = gx + int(((rel_d + win_back) / total_span) * gw)
            y = gy + gh - 4 - int(max(0.0, min(1.0, val_0_1)) * (gh - 8))
            return x, y

        ref = self.state.ref_data
        if ref:
            pts_thr = []
            pts_brk = []
            for r in ref:
                rd = rel_pct(r["LapDistPct"])
                if -win_back <= rd <= win_ahead:
                    pts_thr.append((rd, r["Throttle"]))
                    pts_brk.append((rd, r["Brake"]))

            pts_thr.sort(key=lambda item: item[0])
            pts_brk.sort(key=lambda item: item[0])

            p.setPen(QPen(QColor(50, 255, 100, 130), 1.5, Qt.PenStyle.DotLine))
            for i in range(len(pts_thr) - 1):
                if pts_thr[i + 1][0] - pts_thr[i][0] < 0.03:
                    x1, y1 = to_xy(*pts_thr[i])
                    x2, y2 = to_xy(*pts_thr[i + 1])
                    p.drawLine(x1, y1, x2, y2)

            p.setPen(QPen(QColor(255, 70, 75, 160), 1.8, Qt.PenStyle.DotLine))
            for i in range(len(pts_brk) - 1):
                if pts_brk[i + 1][0] - pts_brk[i][0] < 0.03:
                    x1, y1 = to_xy(*pts_brk[i])
                    x2, y2 = to_xy(*pts_brk[i + 1])
                    p.drawLine(x1, y1, x2, y2)

        trace = list(self.state.player_trace)
        if len(trace) > 2:
            p.setPen(QPen(QColor(45, 255, 95), 2.4))
            for i in range(len(trace) - 1):
                rd1 = rel_pct(trace[i][0])
                rd2 = rel_pct(trace[i + 1][0])
                if -win_back <= rd1 <= 0.01 and -win_back <= rd2 <= 0.01 and abs(rd2 - rd1) < 0.03:
                    x1, y1 = to_xy(rd1, trace[i][1])
                    x2, y2 = to_xy(rd2, trace[i + 1][1])
                    p.drawLine(x1, y1, x2, y2)

            p.setPen(QPen(QColor(255, 55, 65), 2.4))
            for i in range(len(trace) - 1):
                rd1 = rel_pct(trace[i][0])
                rd2 = rel_pct(trace[i + 1][0])
                if -win_back <= rd1 <= 0.01 and -win_back <= rd2 <= 0.01 and abs(rd2 - rd1) < 0.03:
                    x1, y1 = to_xy(rd1, trace[i][2])
                    x2, y2 = to_xy(rd2, trace[i + 1][2])
                    p.drawLine(x1, y1, x2, y2)


# ================= ГЛАВНОЕ ОКНО УПРАВЛЕНИЯ (CONTROL PANEL) =================
class ControlPanelWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("iRacing Coach — Панель управления")
        self.resize(460, 420)
        self.setStyleSheet("""
            QMainWindow, QWidget { background-color: #12161f; color: #e4e8f0; font-family: 'Segoe UI'; }
            QPushButton {
                background-color: #1e2636; border: 1px solid #32405a; border-radius: 6px;
                padding: 8px 12px; font-weight: bold; color: #ffffff;
            }
            QPushButton:hover { background-color: #2a364d; border-color: #00d2ff; }
            QCheckBox { font-size: 13px; spacing: 8px; padding: 4px; }
            QFrame#card { background-color: #181e2a; border: 1px solid #263042; border-radius: 8px; }
        """)

        self.state = TelemetryState()
        self.layout_cfg = load_config()
        self.locked = False
        self._f8_was_down = False
        self._last_beep_zone = -1
        self._brake_hold_active = False

        self.hud_widgets = {
            "banner": BrakeBannerWidget("banner", "Баннер торможения", (610, 40, 440, 82), self.state, self.layout_cfg),
            "delta": DeltaWidget("delta", "Дельта времени", (60, 60, 250, 145), self.state, self.layout_cfg),
            "pedals": PedalsWidget("pedals", "Отдельные Педали", (60, 680, 210, 260), self.state, self.layout_cfg),
            "steer": SteerGearRibbonWidget("steer", "Кокпит: 3D-Траектория + Руль + Педали", (590, 560, 460, 310), self.state, self.layout_cfg),
            "graph": TelemetryGraphWidget("graph", "График телеметрии", (1180, 700, 460, 230), self.state, self.layout_cfg),
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
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

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
        self.btn_lock = QPushButton("🔓 F8: Перемещение виджетов")
        self.btn_lock.clicked.connect(self.toggle_lock)
        btn_row.addWidget(self.btn_load)
        btn_row.addWidget(self.btn_lock)
        root.addLayout(btn_row)

        chk_card = QFrame()
        chk_card.setObjectName("card")
        chk_layout = QVBoxLayout(chk_card)
        lbl_h = QLabel("Отображаемые виджеты на экране:")
        lbl_h.setStyleSheet("font-weight: bold; color: #00d2ff; margin-bottom: 4px;")
        chk_layout.addWidget(lbl_h)

        self.checkboxes = {}
        titles = {
            "banner": "Центральный баннер торможения (ТОРМОЗИ + Звук)",
            "delta": "Виджет Дельты времени и скорости (DELTA)",
            "steer": "Центральный кокпит (3D-Траектория + Руль + Встроенные педали)",
            "pedals": "Отдельный блок педалей (можно отключить, т.к. есть в кокпите)",
            "graph": "Живой график педалей по дистанции (LIVE vs REF)",
        }
        for key, text in titles.items():
            cb = QCheckBox(text)
            cb.setChecked(True)
            cb.toggled.connect(lambda checked, k=key: self.toggle_widget(k, checked))
            chk_layout.addWidget(cb)
            self.checkboxes[key] = cb

        root.addWidget(chk_card)

        hint = QLabel("💡 Совет: расставь виджеты мышкой поверх игры и нажми клавишу F8 — рамки исчезнут, а клики мыши будут проходить сквозь оверлей.")
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 11px; color: #7f8c9f;")
        root.addWidget(hint)
        root.addStretch()

    def toggle_widget(self, key: str, checked: bool):
        w = self.hud_widgets[key]
        w.setVisible(checked)
        if key not in self.layout_cfg:
            self.layout_cfg[key] = {"x": w.x(), "y": w.y(), "w": w.width(), "h": w.height()}
        self.layout_cfg[key]["enabled"] = checked
        save_config(self.layout_cfg)

    def toggle_lock(self):
        self.locked = not self.locked
        for w in self.hud_widgets.values():
            if w.isVisible():
                w.set_locked(self.locked)
        if self.locked:
            self.btn_lock.setText("🔒 F8: ЗАБЛОКИРОВАНО (клики сквозь)")
            self.btn_lock.setStyleSheet("background-color: #195c36; border-color: #2ecc71;")
        else:
            self.btn_lock.setText("🔓 F8: Перемещение виджетов")
            self.btn_lock.setStyleSheet("")

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
            f"Эталон: {os.path.basename(path)} ({len(rows)} точек, время ~{lap_dur:.1f} с, зон торможения: {len(self.state.brake_zones)})"
        )

    def _detect_brake_zones(self, rows):
        """Находит индексы НАЧАЛА зон торможения (переход Brake с <0.12 на >0.25)."""
        zones = []
        in_zone = False
        for i, r in enumerate(rows):
            b = r["Brake"]
            if not in_zone and b > 0.25:
                zones.append(i)
                in_zone = True
            elif in_zone and b < 0.08:
                in_zone = False
        return zones

    def _poll_global_f8(self):
        try:
            state = ctypes.windll.user32.GetAsyncKeyState(0x77)  # VK_F8
            is_down = (state & 0x8000) != 0
            if is_down and not self._f8_was_down:
                self.toggle_lock()
            self._f8_was_down = is_down
        except Exception:
            pass

    def tick(self):
        self._poll_global_f8()

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
        self.state.steer_rad = float(self.irc["SteeringWheelAngle"] or 0.0)
        self.state.gear = int(self.irc["Gear"] or 0)
        self.state.lap_time = float(self.irc["LapCurrentLapTime"] or 0.0)

        self.state.player_trace.append((self.state.lap_pct, self.state.throttle, self.state.brake))

        if self.state.ref_data:
            t = get_target_state(self.state.lap_pct, self.state.ref_data)
            self.state.t_speed = t["Speed"]
            self.state.t_throttle = t["Throttle"]
            self.state.t_brake = t["Brake"]
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
        """
        Управляет баннером без провалов (исчезновений) между таймером и нажатием тормоза:
        как только таймер дошёл до <= 0.35 с ИЛИ эталон начал тормозить (> 10%),
        включается удержание режима BRAKE_NOW, которое горит непрерывно, пока эталон не отпустит тормоз (< 8%).
        """
        # 1. Если мы уже внутри активного торможения ИЛИ эталон прямо сейчас держит тормоз (> 12%)
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

        # 2. Ищем ближайшую ТОЧКУ НАЧАЛА торможения впереди по трассе (от 0.0 до 20% круга)
        best_dpct = None
        best_zone_id = -1
        for z_idx in self.state.brake_zones:
            z_pct = self.state.ref_data[z_idx]["LapDistPct"]
            d = z_pct - self.state.lap_pct
            if d < 0:
                d += 1.0
            if 0.0 <= d < 0.20 and (best_dpct is None or d < best_dpct):
                best_dpct = d
                best_zone_id = z_idx

        if best_dpct is not None and speed_ms > 4.0:
            dist_m = best_dpct * self.state.track_len
            sec = dist_m / speed_ms
            if sec <= 3.2:
                self.state.brake_in_sec = sec
                self.state.brake_dist_m = dist_m
                if sec <= 0.35:
                    # Плавный бесшовный переход в BRAKE_NOW до того, как машина пересечёт точку
                    self._brake_hold_active = True
                    self.state.banner_mode = "BRAKE_NOW"
                else:
                    self.state.banner_mode = "BRAKE_SOON"

                if sec <= 1.1 and self._last_beep_zone != best_zone_id:
                    self._last_beep_zone = best_zone_id
                    if winsound:
                        threading.Thread(target=lambda: winsound.Beep(880, 95), daemon=True).start()
                return

        # 3. Подсказка полного газа на выходе
        if self.state.t_brake < 0.05 and self.state.t_throttle > 0.90 and self.state.throttle < 0.70:
            self.state.banner_mode = "THROTTLE"

    def closeEvent(self, event):
        for w in self.hud_widgets.values():
            w.close()
        self.irc.shutdown()
        event.accept()


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    win = ControlPanelWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()