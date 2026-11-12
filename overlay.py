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
    QLabel, QPushButton, QCheckBox, QFileDialog, QFrame, QSlider
)
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QFont, QBrush, QPainterPath,
    QPolygonF, QLinearGradient
)

import irsdk
from reference import load_garage61_csv, get_target_state

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "hud_layout.json")
FONT_UI = "Segoe UI"
FONT_MONO = "Consolas"

# Таблица виртуальных кодов клавиш Windows (VK) для биндов
VK_NAMES = {
    0x70: "F1", 0x71: "F2", 0x72: "F3", 0x73: "F4", 0x74: "F5", 0x75: "F6",
    0x76: "F7", 0x77: "F8", 0x78: "F9", 0x79: "F10", 0x7A: "F11", 0x7B: "F12",
    0xC0: "~ (Тильда)", 0x09: "TAB", 0x14: "CAPS", 0x20: "SPACE",
    0x2D: "INSERT", 0x2E: "DELETE", 0x24: "HOME", 0x23: "END",
    0x21: "PGUP", 0x22: "PGDN", 0x6A: "NUM *", 0x6B: "NUM +", 0x6D: "NUM -",
}
for code in range(0x30, 0x3A):
    VK_NAMES[code] = chr(code)
for code in range(0x41, 0x5B):
    VK_NAMES[code] = chr(code)
for i in range(10):
    VK_NAMES[0x60 + i] = f"NUM {i}"


def vk_to_name(vk: int) -> str:
    return VK_NAMES.get(vk, f"KEY_{vk}")


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


class TelemetryState:
    def __init__(self):
        self.connected = False
        self.lap_pct = 0.0
        self.speed_kmh = 0.0
        self.throttle = 0.0
        self.brake = 0.0
        self.clutch = 0.0
        self.steer_rad = 0.0
        self.gear = 0
        self.lap_time = 0.0
        self.track_len = 4500.0

        # Информация о кругах и позиции в гонке
        self.current_lap = 0
        self.total_laps = 0        # 0 или >= 9999 означает практику / бесконечность
        self.laps_remain = -1      # -1 = бесконечность (∞)
        self.position = 0          # Позиция игрока (P1, P2...)

        # Настройки отображения
        self.show_clutch = False
        self.warn_lead_sec = 5.0

        # Эталон в текущей точке
        self.t_speed = 0.0
        self.t_throttle = 0.0
        self.t_brake = 0.0
        self.t_clutch = 0.0
        self.t_steer_rad = 0.0
        self.t_gear = 0
        self.t_time = 0.0
        self.ref_idx = 0

        # Метрики тренера
        self.delta_sec = 0.0
        self.delta_speed = 0.0
        self.brake_in_sec = None
        self.brake_dist_m = None
        self.banner_mode = ""

        self.ref_data = []
        self.brake_zones = []
        self.player_trace = deque(maxlen=260)  # (lap_pct, throttle, brake, clutch, speed)
        self.delta_history = deque(maxlen=120)


class DraggableHUDWidget(QWidget):
    """Прозрачный голографический виджет с поддержкой перемещения, изменения размера мышкой и F8."""
    RESIZE_MARGIN = 18

    def __init__(self, key_name, title, default_rect, state, layout_cfg, default_bg_pct=75, min_size=(170, 90)):
        super().__init__()
        self.key_name = key_name
        self.hud_title = title
        self.state = state
        self.layout_cfg = layout_cfg
        self.locked = False
        self._drag_pos = None
        self._resizing = False
        self._resize_start_mouse = None
        self._resize_start_geom = None
        self.setMinimumSize(min_size[0], min_size[1])
        self.setMouseTracking(True)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        saved = self.layout_cfg.get(self.key_name, {})
        x = saved.get("x", default_rect[0])
        y = saved.get("y", default_rect[1])
        w = max(min_size[0], saved.get("w", default_rect[2]))
        h = max(min_size[1], saved.get("h", default_rect[3]))
        self.bg_opacity_pct = saved.get("bg_opacity", default_bg_pct)
        self.fg_opacity_pct = saved.get("fg_opacity", 100)
        self.setGeometry(x, y, w, h)

    def _in_resize_zone(self, local_pos) -> bool:
        return (
            not self.locked
            and local_pos.x() >= self.width() - self.RESIZE_MARGIN
            and local_pos.y() >= self.height() - self.RESIZE_MARGIN
        )

    def set_bg_opacity(self, pct: int):
        self.bg_opacity_pct = max(0, min(100, int(pct)))
        self._save_state()
        self.update()

    def set_fg_opacity(self, pct: int):
        self.fg_opacity_pct = max(10, min(100, int(pct)))
        self._save_state()
        self.update()

    def set_locked(self, locked: bool):
        self.locked = locked
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, locked)
        self.show()
        self.update()

    def _save_state(self):
        self.layout_cfg[self.key_name] = {
            "x": self.x(), "y": self.y(), "w": self.width(), "h": self.height(),
            "enabled": self.layout_cfg.get(self.key_name, {}).get("enabled", True),
            "bg_opacity": self.bg_opacity_pct,
            "fg_opacity": self.fg_opacity_pct,
        }
        save_config(self.layout_cfg)

    def mousePressEvent(self, event):
        if self.locked or event.button() != Qt.MouseButton.LeftButton:
            return
        local_pt = event.position().toPoint()
        if self._in_resize_zone(local_pt):
            self._resizing = True
            self._resize_start_mouse = event.globalPosition().toPoint()
            self._resize_start_geom = self.geometry()
        else:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self.locked:
            return
        local_pt = event.position().toPoint()
        if self._resizing and self._resize_start_mouse is not None:
            delta = event.globalPosition().toPoint() - self._resize_start_mouse
            new_w = max(self.minimumWidth(), self._resize_start_geom.width() + delta.x())
            new_h = max(self.minimumHeight(), self._resize_start_geom.height() + delta.y())
            self.resize(new_w, new_h)
            self.update()
            return
        if self._drag_pos is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            return

        if self._in_resize_zone(local_pt):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event):
        if self._drag_pos is not None or self._resizing:
            self._drag_pos = None
            self._resizing = False
            self._save_state()

    def begin_hud_painter(self) -> QPainter:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self.fg_opacity_pct / 100.0)
        return p

    def draw_card_bg(self, p: QPainter, max_alpha=215):
        """Футуристичная стеклянная карточка в стиле Holo-Glass (скрин №2)."""
        w, h = self.width(), self.height()
        path = QPainterPath()
        path.addRoundedRect(QRectF(1.5, 1.5, w - 3, h - 3), 12, 12)
        bg_scale = self.bg_opacity_pct / 100.0
        alpha = int(max_alpha * bg_scale)

        if alpha > 0:
            grad = QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0.0, QColor(16, 28, 36, alpha))
            grad.setColorAt(0.5, QColor(10, 16, 22, int(alpha * 0.92)))
            grad.setColorAt(1.0, QColor(6, 10, 15, alpha))
            p.fillPath(path, QBrush(grad))

            # Мягкий неоновый блик по верхней грани карточки
            top_glow = QLinearGradient(0, 0, w, 0)
            top_glow.setColorAt(0.0, QColor(0, 240, 255, 0))
            top_glow.setColorAt(0.25, QColor(80, 245, 255, int(130 * bg_scale)))
            top_glow.setColorAt(0.75, QColor(80, 245, 255, int(90 * bg_scale)))
            top_glow.setColorAt(1.0, QColor(0, 240, 255, 0))
            p.setPen(QPen(QBrush(top_glow), 1.8))
            p.drawLine(14, 2, w - 14, 2)

        if not self.locked:
            p.setPen(QPen(QColor(0, 225, 255, 200), 1.5, Qt.PenStyle.DashLine))
            p.drawPath(path)
            p.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
            p.setPen(QColor(0, 230, 255, 230))
            p.drawText(10, 15, f"✥ {self.hud_title}")

            # Уголок растягивания размера (справа внизу)
            p.setPen(QPen(QColor(0, 235, 255, 230), 2))
            for offset in (4, 8, 12):
                p.drawLine(w - offset, h - 3, w - 3, h - offset)
        elif alpha > 12:
            p.setPen(QPen(QColor(110, 210, 230, int(55 * bg_scale)), 1.1))
            p.drawPath(path)


# ================= 1. ЦЕНТРАЛЬНЫЙ БАННЕР ТОРМОЖЕНИЯ =================
class BrakeBannerWidget(DraggableHUDWidget):
    def paintEvent(self, event):
        p = self.begin_hud_painter()
        w, h = self.width(), self.height()

        mode = self.state.banner_mode
        if not self.state.connected or (not mode and self.locked):
            if not self.locked:
                self.draw_card_bg(p, 180)
                p.setPen(QColor(180, 195, 210))
                p.setFont(QFont(FONT_UI, 11, QFont.Weight.Bold))
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "БАННЕР ТОРМОЖЕНИЯ (тяни за правый нижний угол ◢)")
            return

        path = QPainterPath()
        path.addRoundedRect(QRectF(2, 2, w - 4, h - 4), 12, 12)
        bg_scale = self.bg_opacity_pct / 100.0
        lead_max = max(2.0, self.state.warn_lead_sec)

        if mode == "BRAKE_NOW":
            bg = QColor(195, 18, 30, int(235 * bg_scale))
            border = QColor(255, 85, 95)
            title = "ТОРМОЗИ!"
            ref_b = self.state.t_brake * 100.0
            you_b = self.state.brake * 100.0
            sub = f"ЦЕЛЬ: {ref_b:.0f}%   |   ТЫ: {you_b:.0f}%"
            pct = max(0.05, min(1.0, self.state.t_brake))
            bar_col = QColor(255, 225, 65)
        elif mode == "BRAKE_SOON":
            sec = self.state.brake_in_sec or 0.0
            dist = self.state.brake_dist_m or 0.0
            bg = QColor(50, 22, 10, int(225 * bg_scale))
            border = QColor(255, 150, 40)
            title = f"ТОРМОЗ ЧЕРЕЗ {sec:.1f} с"
            sub = f"ДИСТАНЦИЯ: {dist:.0f} м"
            pct = max(0.0, min(1.0, 1.0 - (sec / lead_max)))
            bar_col = QColor(255, 155, 40)
        elif mode == "BRAKE_PREP":
            sec = self.state.brake_in_sec or 0.0
            dist = self.state.brake_dist_m or 0.0
            bg = QColor(14, 30, 44, int(215 * bg_scale))
            border = QColor(0, 215, 255)
            title = f"ГОТОВЬСЯ: {sec:.1f} с"
            sub = f"ДО ТОЧКИ ТОРМОЖЕНИЯ: {dist:.0f} м"
            pct = max(0.05, min(1.0, 1.0 - (sec / lead_max)))
            bar_col = QColor(0, 215, 255)
        elif mode == "THROTTLE":
            bg = QColor(14, 75, 38, int(210 * bg_scale))
            border = QColor(55, 255, 125)
            title = "ПОЛНЫЙ ГАЗ"
            sub = "ВЫХОД ИЗ ПОВОРОТА"
            pct = 1.0
            bar_col = QColor(55, 255, 125)
        else:
            self.draw_card_bg(p, 160)
            return

        if bg.alpha() > 0:
            p.fillPath(path, bg)
        if bg_scale > 0.05 or not self.locked:
            p.setPen(QPen(QColor(border.red(), border.green(), border.blue(), 80), 5))
            p.drawPath(path)
            p.setPen(QPen(border, 2.0))
            p.drawPath(path)

        title_fs = max(12, min(24, int(h * 0.23)))
        p.setPen(Qt.GlobalColor.white)
        p.setFont(QFont(FONT_UI, title_fs, QFont.Weight.Bold))
        p.drawText(0, int(h * 0.06), w, int(h * 0.42), Qt.AlignmentFlag.AlignCenter, title)

        bar_w = w - 56
        bar_x = 28
        bar_y = int(h * 0.53)
        bar_h = max(8, int(h * 0.14))
        p.fillRect(bar_x, bar_y, bar_w, bar_h, QColor(0, 0, 0, 155))
        p.fillRect(bar_x, bar_y, int(bar_w * pct), bar_h, bar_col)

        if mode == "BRAKE_NOW":
            you_x = bar_x + int(bar_w * max(0.0, min(1.0, self.state.brake)))
            p.setPen(QPen(Qt.GlobalColor.white, 3))
            p.drawLine(you_x, bar_y - 2, you_x, bar_y + bar_h + 2)

        p.setFont(QFont(FONT_MONO, max(8, int(h * 0.13)), QFont.Weight.Bold))
        p.setPen(QColor(235, 242, 250))
        p.drawText(0, int(h * 0.70), w, int(h * 0.26), Qt.AlignmentFlag.AlignCenter, sub)


# ================= 2. ВИДЖЕТ ДЕЛЬТЫ + КРУГ + ПОЗИЦИЯ =================
class DeltaWidget(DraggableHUDWidget):
    def paintEvent(self, event):
        p = self.begin_hud_painter()
        self.draw_card_bg(p, 215)
        w, h = self.width(), self.height()

        p.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
        p.setPen(QColor(150, 175, 195))
        p.drawText(14, 8, w - 28, 18, Qt.AlignmentFlag.AlignLeft, "DELTA TIME vs REF")

        # Позиция и Круг (справа вверху и отдельной строкой)
        pos_str = f"P{self.state.position}" if self.state.position > 0 else "P--"
        cur_lap = max(0, self.state.current_lap)
        if self.state.total_laps > 0 and self.state.total_laps < 9999:
            tot_str = str(self.state.total_laps)
            rem_str = str(max(0, self.state.laps_remain))
        else:
            tot_str = "∞"
            rem_str = "∞"

        p.setFont(QFont(FONT_MONO, 9, QFont.Weight.Bold))
        p.setPen(QColor(0, 230, 255))
        p.drawText(14, 8, w - 28, 18, Qt.AlignmentFlag.AlignRight, f"POS {pos_str}")

        # Главная цифра Дельты с неоновым свечением
        d = self.state.delta_sec
        if abs(d) > 25.0 or not self.state.connected:
            txt = "Δ --.-- s"
            col = QColor(175, 185, 195)
        else:
            txt = f"Δ {d:+.2f} s"
            col = QColor(55, 255, 125) if d <= 0 else QColor(255, 75, 80)

        main_fs = max(16, min(32, int(h * 0.18)))
        p.setFont(QFont(FONT_MONO, main_fs, QFont.Weight.Bold))
        p.setPen(col)
        p.drawText(14, 24, w - 28, int(h * 0.28), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, txt)

        # Разница скорости
        ds = self.state.delta_speed
        p.setFont(QFont(FONT_UI, max(9, int(h * 0.075))))
        p.setPen(QColor(210, 220, 230))
        spd_y = int(h * 0.48)
        p.drawText(14, spd_y, w - 28, 20, Qt.AlignmentFlag.AlignLeft, f"Скорость: {ds:+.0f} км/ч")

        # Горизонтальный неоновый разделитель (как на скрине 2)
        line_y = spd_y + 22
        p.setPen(QPen(QColor(180, 220, 240, 110), 1.2))
        p.drawLine(14, line_y, w - 14, line_y)

        # Инфо по кругам: Текущий круг / Всего (Осталось)
        p.setFont(QFont(FONT_MONO, max(9, int(h * 0.075)), QFont.Weight.Bold))
        p.setPen(QColor(235, 245, 255))
        p.drawText(14, line_y + 4, w - 28, 20, Qt.AlignmentFlag.AlignLeft,
                   f"КРУГ: {cur_lap}/{tot_str}  (ОСТ: {rem_str})")

        # Мини-график истории дельты внизу
        gx, gy = 14, line_y + 26
        gw, gh = w - 28, h - gy - 10
        if gh > 14:
            if self.bg_opacity_pct > 10:
                p.fillRect(gx, gy, gw, gh, QColor(6, 10, 14, int(140 * (self.bg_opacity_pct / 100.0))))
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


# ================= 3. ОТДЕЛЬНЫЙ ВИДЖЕТ ПЕДАЛЕЙ =================
class PedalsWidget(DraggableHUDWidget):
    def paintEvent(self, event):
        p = self.begin_hud_painter()
        self.draw_card_bg(p, 210)
        w, h = self.width(), self.height()

        p.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
        p.setPen(QColor(170, 190, 210))
        p.drawText(10, 6, w - 20, 18, Qt.AlignmentFlag.AlignCenter, "PEDALS: YOU vs REF")

        top_y = 46
        bot_y = h - 24
        bar_h = max(24, bot_y - top_y)

        if self.state.show_clutch:
            bw = max(20, min(38, int(w * 0.16)))
            draw_unified_pedal_bar(
                p, x_center=w // 6 + 4, top_y=top_y, bar_w=bw, bar_h=bar_h,
                you_val=self.state.clutch, ref_val=self.state.t_clutch,
                label="CLUTCH", main_col=QColor(0, 210, 255)
            )
            draw_unified_pedal_bar(
                p, x_center=w // 2, top_y=top_y, bar_w=bw, bar_h=bar_h,
                you_val=self.state.brake, ref_val=self.state.t_brake,
                label="BRAKE", main_col=QColor(255, 45, 60)
            )
            draw_unified_pedal_bar(
                p, x_center=(w * 5) // 6 - 4, top_y=top_y, bar_w=bw, bar_h=bar_h,
                you_val=self.state.throttle, ref_val=self.state.t_throttle,
                label="THROTTLE", main_col=QColor(40, 250, 110)
            )
        else:
            bw = max(24, min(46, int(w * 0.22)))
            draw_unified_pedal_bar(
                p, x_center=w // 4 + 4, top_y=top_y, bar_w=bw, bar_h=bar_h,
                you_val=self.state.brake, ref_val=self.state.t_brake,
                label="BRAKE", main_col=QColor(255, 45, 60)
            )
            draw_unified_pedal_bar(
                p, x_center=(w * 3) // 4 - 4, top_y=top_y, bar_w=bw, bar_h=bar_h,
                you_val=self.state.throttle, ref_val=self.state.t_throttle,
                label="THROTTLE", main_col=QColor(40, 250, 110)
            )


def draw_unified_pedal_bar(p: QPainter, x_center: int, top_y: int, bar_w: int, bar_h: int,
                           you_val: float, ref_val: float, label: str, main_col: QColor):
    """
    ИНВЕРТИРОВАННАЯ ВИЗУАЛИЗАЦИЯ (по запросу):
    - Сзади на всю ширину стакана (полупрозрачный фон с контуром) — ТВОЁ нажатие (YOU).
    - Спереди внутри стакана (яркий, сочный неоновый градиент + светящаяся белая крышка) — ЭТАЛОН (REF).
    """
    x0 = x_center - bar_w // 2
    you_v = max(0.0, min(1.0, you_val))
    ref_v = max(0.0, min(1.0, ref_val))

    # Цифры сверху: ТЫ / ЭТАЛОН%
    p.setFont(QFont(FONT_MONO, 8 if bar_w < 26 else 9, QFont.Weight.Bold))
    p.setPen(QColor(225, 235, 245))
    p.drawText(x_center - 46, top_y - 20, 92, 16, Qt.AlignmentFlag.AlignCenter,
               f"{you_v * 100:.0f}/{ref_v * 100:.0f}%")

    # Тёмная стеклянная колба стакана
    p.fillRect(x0, top_y, bar_w, bar_h, QColor(10, 15, 22, 195))

    # 1. ЗАДНИЙ ФОН — ТВОЁ НАЖАТИЕ (YOU): спокойная полупрозрачная заливка на всю ширину стакана + тонкая кромка
    yh = int(bar_h * you_v)
    if yh > 0:
        you_bg = QColor(main_col.red(), main_col.green(), main_col.blue(), 75)
        p.fillRect(x0 + 1, top_y + bar_h - yh, bar_w - 2, yh, you_bg)
        # Тонкая пунктирная риска твоего уровня
        uy = top_y + bar_h - yh
        p.setPen(QPen(QColor(255, 255, 255, 160), 1.5))
        p.drawLine(x0, uy, x0 + bar_w, uy)

    # 2. ПЕРЕДНИЙ ПЛАН — ЭТАЛОН (REF): яркий цветастый неоновый столбик с градиентом и свечением!
    rh = int(bar_h * ref_v)
    pad = max(3, bar_w // 6)
    if rh > 0:
        ry = top_y + bar_h - rh
        grad = QLinearGradient(0, ry, 0, top_y + bar_h)
        grad.setColorAt(0.0, QColor(min(255, main_col.red() + 55), min(255, main_col.green() + 55), min(255, main_col.blue() + 55), 255))
        grad.setColorAt(0.3, QColor(main_col.red(), main_col.green(), main_col.blue(), 245))
        grad.setColorAt(1.0, QColor(int(main_col.red() * 0.55), int(main_col.green() * 0.55), int(main_col.blue() * 0.55), 230))
        p.fillRect(QRectF(x0 + pad, ry, bar_w - pad * 2, rh), QBrush(grad))

        # Яркая неоновая белая шапка на вершине эталона (как на скрине 2)
        p.setPen(QPen(QColor(main_col.red(), main_col.green(), main_col.blue(), 140), 5))
        p.drawLine(x0 - 1, ry, x0 + bar_w + 1, ry)
        p.setPen(QPen(Qt.GlobalColor.white, 2.5))
        p.drawLine(x0 - 2, ry, x0 + bar_w + 2, ry)

    # Рамка стакана
    diff = abs(you_v - ref_v)
    border_col = QColor(60, 255, 180, 220) if (ref_v > 0.1 and diff < 0.08) else QColor(110, 140, 165, 150)
    p.setPen(QPen(border_col, 1.3))
    p.drawRect(x0, top_y, bar_w, bar_h)

    # Подпись снизу
    p.setFont(QFont(FONT_UI, 7 if bar_w < 26 else 8, QFont.Weight.Bold))
    p.setPen(QColor(200, 215, 230))
    p.drawText(x_center - 42, top_y + bar_h + 4, 84, 16, Qt.AlignmentFlag.AlignCenter, label)


# ================= 4. ГЛАВНЫЙ КОКПИТ-ВИДЖЕТ (3D-ТРАЕКТОРИЯ + РУЛЬ + ПЕДАЛИ + ПЕРЕДАЧА) =================
class SteerGearRibbonWidget(DraggableHUDWidget):
    def paintEvent(self, event):
        p = self.begin_hud_painter()
        self.draw_card_bg(p, 200)
        w, h = self.width(), self.height()

        pedal_top = int(h * 0.14)
        pedal_h = int(h * 0.76)

        if self.state.show_clutch:
            bw1 = max(18, int(w * 0.045))
            bw2 = max(20, int(w * 0.052))
            x_c1 = int(w * 0.055)
            x_c2 = int(w * 0.135)
            draw_unified_pedal_bar(
                p, x_center=x_c1, top_y=pedal_top, bar_w=bw1, bar_h=pedal_h,
                you_val=self.state.clutch, ref_val=self.state.t_clutch,
                label="СЦЕПЛ", main_col=QColor(0, 210, 255)
            )
            draw_unified_pedal_bar(
                p, x_center=x_c2, top_y=pedal_top, bar_w=bw2, bar_h=pedal_h,
                you_val=self.state.brake, ref_val=self.state.t_brake,
                label="ТОРМ", main_col=QColor(255, 45, 60)
            )
            center_left = x_c2 + bw2 // 2 + 16
        else:
            bw = max(24, int(w * 0.065))
            x_b = int(w * 0.085)
            draw_unified_pedal_bar(
                p, x_center=x_b, top_y=pedal_top, bar_w=bw, bar_h=pedal_h,
                you_val=self.state.brake, ref_val=self.state.t_brake,
                label="ТОРМОЗ", main_col=QColor(255, 45, 60)
            )
            center_left = x_b + bw // 2 + 18

        bw_thr = max(26, int(w * 0.068))
        x_t = w - int(w * 0.085)
        draw_unified_pedal_bar(
            p, x_center=x_t, top_y=pedal_top, bar_w=bw_thr, bar_h=pedal_h,
            you_val=self.state.throttle, ref_val=self.state.t_throttle,
            label="ГАЗ", main_col=QColor(40, 250, 110)
        )

        center_right = x_t - bw_thr // 2 - 18
        center_w = max(120, center_right - center_left)

        # 1. Голографическая 3D-лента траектории с сеткой (верхние 56% высоты)
        self._draw_trajectory_ribbon(p, center_left, center_w, int(h * 0.56))

        # 2. Неоновая дуга угла руля со свечением (как на скрине 2)
        arc_w = min(int(center_w * 0.88), int(h * 0.78))
        arc_h = int(arc_w * 0.39)
        arc_x = center_left + (center_w - arc_w) // 2
        arc_y = int(h * 0.50)

        rect_outer = QRectF(arc_x, arc_y, arc_w, arc_h * 2)
        rect_inner = QRectF(arc_x + 9, arc_y + 9, arc_w - 18, (arc_h - 9) * 2)

        # Подложка дуги
        p.setPen(QPen(QColor(40, 60, 75, 160), 6))
        p.drawArc(rect_outer, 0 * 16, 180 * 16)
        p.setPen(QPen(QColor(35, 50, 65, 150), 4))
        p.drawArc(rect_inner, 0 * 16, 180 * 16)

        you_deg = -math.degrees(self.state.steer_rad)
        ref_deg = -math.degrees(self.state.t_steer_rad)

        def deg_to_qt_angle(deg_right_positive):
            clamped = max(-180.0, min(180.0, deg_right_positive))
            return int((90.0 - clamped * 0.45) * 16)

        # Засечка центра (12 часов) со свечением
        cx_arc = arc_x + arc_w // 2
        p.setPen(QPen(QColor(255, 255, 255, 90), 5))
        p.drawLine(cx_arc, arc_y - 5, cx_arc, arc_y + 15)
        p.setPen(QPen(Qt.GlobalColor.white, 2))
        p.drawLine(cx_arc, arc_y - 5, cx_arc, arc_y + 15)

        # Сектор эталона (оранжевое неоновое свечение от центра до угла эталона + яркий маркер)
        ref_ang = deg_to_qt_angle(ref_deg)
        span_ref = ref_ang - (90 * 16)
        p.setPen(QPen(QColor(255, 150, 30, 70), 10))
        p.drawArc(rect_inner, 90 * 16, span_ref)
        p.setPen(QPen(QColor(255, 165, 45, 230), 4))
        p.drawArc(rect_inner, 90 * 16, span_ref)
        p.setPen(QPen(QColor(255, 220, 120), 7))
        p.drawArc(rect_inner, ref_ang - 5 * 16, 10 * 16)

        # Сектор игрока (голубое неоновое свечение + яркий маркер)
        you_ang = deg_to_qt_angle(you_deg)
        span_you = you_ang - (90 * 16)
        diff = abs(you_deg - ref_deg)
        you_col = QColor(0, 225, 255) if diff < 14 else QColor(255, 70, 80)
        p.setPen(QPen(QColor(you_col.red(), you_col.green(), you_col.blue(), 75), 11))
        p.drawArc(rect_outer, 90 * 16, span_you)
        p.setPen(QPen(you_col, 4.5))
        p.drawArc(rect_outer, 90 * 16, span_you)
        p.setPen(QPen(Qt.GlobalColor.white, 7))
        p.drawArc(rect_outer, you_ang - 4 * 16, 8 * 16)

        # Подписи ТЫ (РУЛЬ) и ЭТАЛОН
        p.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        p.setPen(QColor(255, 110, 95) if diff >= 14 else you_col)
        p.drawText(center_left, arc_y - 4, 90, 14, Qt.AlignmentFlag.AlignLeft, "ТЫ (РУЛЬ)")
        p.setFont(QFont(FONT_MONO, 13, QFont.Weight.Bold))
        p.drawText(center_left, arc_y + 10, 90, 22, Qt.AlignmentFlag.AlignLeft, f"{you_deg:+.0f}°")

        p.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        p.setPen(QColor(255, 180, 70))
        p.drawText(center_left + center_w - 90, arc_y - 4, 90, 14, Qt.AlignmentFlag.AlignRight, "ЭТАЛОН")
        p.setFont(QFont(FONT_MONO, 13, QFont.Weight.Bold))
        p.drawText(center_left + center_w - 90, arc_y + 10, 90, 22, Qt.AlignmentFlag.AlignRight, f"{ref_deg:+.0f}°")

        # 3. Блок Передачи и Скорости внутри дуги
        gear_y = int(h * 0.69)
        g_you = self.state.gear
        g_ref = self.state.t_gear
        g_you_str = "R" if g_you == -1 else "N" if g_you == 0 else str(g_you)
        g_ref_str = str(g_ref) if g_ref > 0 else "-"

        if g_ref > 0 and g_you != g_ref:
            arrow = "↓" if g_ref < g_you else "↑"
            g_text = f"{g_you_str} → {g_ref_str} {arrow}"
            g_col = QColor(255, 165, 45) if g_ref < g_you else QColor(70, 255, 130)
        else:
            g_text = f"GEAR {g_you_str}"
            g_col = QColor(240, 248, 255)

        gear_fs = max(15, min(26, int(h * 0.075)))
        p.setFont(QFont(FONT_MONO, gear_fs, QFont.Weight.Bold))
        p.setPen(g_col)
        p.drawText(center_left, gear_y, center_w, 32, Qt.AlignmentFlag.AlignCenter, g_text)

        spd_fs = max(10, min(15, int(h * 0.042)))
        p.setFont(QFont(FONT_MONO, spd_fs, QFont.Weight.Bold))
        p.setPen(QColor(255, 200, 90) if abs(self.state.delta_speed) > 5 else QColor(180, 240, 210))
        p.drawText(center_left, gear_y + 30, center_w, 24, Qt.AlignmentFlag.AlignCenter,
                   f"{self.state.speed_kmh:.0f} км/ч  (эталон: {self.state.t_speed:.0f})")

    def _draw_trajectory_ribbon(self, p: QPainter, x_offset: int, w_zone: int, h_zone: int):
        """Футуристичная 3D-дорожка с голографической сеткой (как на скрине 2)."""
        ref = self.state.ref_data
        if not ref:
            return

        n = len(ref)
        idx0 = self.state.ref_idx
        steps = 24
        lookahead_pct = min(0.06, 160.0 / max(1000.0, self.state.track_len))
        step_stride = max(1, int((n * lookahead_pct) / steps))

        cx = x_offset + w_zone * 0.5
        bottom_y = h_zone - 2
        top_y = 12

        curve_x = 0.0
        slope = 0.0
        pts_left, pts_right, pts_center = [], [], []
        pts_q1, pts_q3 = [], []  # Внутренние продольные линии сетки
        seg_data = []

        for s in range(steps):
            pt = ref[(idx0 + s * step_stride) % n]
            t = s / float(steps - 1)
            persp_t = math.pow(t, 0.80)
            y = bottom_y - persp_t * (bottom_y - top_y)

            steer_right_deg = -math.degrees(pt["SteeringWheelAngle"])
            slope += (steer_right_deg / 90.0) * 3.8
            curve_x += slope * (0.35 + 0.65 * t)
            max_offset = w_zone * 0.38
            curve_x = max(-max_offset, min(max_offset, curve_x))

            half_w = (1.0 - t * 0.76) * (w_zone * 0.21)
            pts_left.append(QPointF(cx + curve_x - half_w, y))
            pts_right.append(QPointF(cx + curve_x + half_w, y))
            pts_center.append(QPointF(cx + curve_x, y))
            pts_q1.append(QPointF(cx + curve_x - half_w * 0.5, y))
            pts_q3.append(QPointF(cx + curve_x + half_w * 0.5, y))

            seg_data.append((pt["Brake"], pt["Throttle"], t))

        for s in range(steps - 1):
            brk, thr, t = seg_data[s]
            fade = 1.0 - t * 0.62
            alpha = int(205 * fade)

            if brk > 0.30:
                fill_col = QColor(255, 40, 55, int(alpha * 0.85))
                edge_col = QColor(255, 110, 120, int(230 * fade))
            elif brk > 0.08:
                fill_col = QColor(255, 155, 30, int(alpha * 0.80))
                edge_col = QColor(255, 200, 90, int(220 * fade))
            elif thr > 0.15:
                fill_col = QColor(30, 215, 95, int(alpha * 0.78))
                edge_col = QColor(90, 255, 150, int(220 * fade))
            else:
                fill_col = QColor(0, 180, 235, int(alpha * 0.60))
                edge_col = QColor(80, 230, 255, int(200 * fade))

            poly = QPolygonF([pts_left[s], pts_right[s], pts_right[s + 1], pts_left[s + 1]])
            p.setBrush(QBrush(fill_col))
            p.setPen(QPen( QColor(255, 255, 255, int(45 * fade)), 1))
            p.drawPolygon(poly)

            # Боковые светящиеся бортики и внутренние продольные нити сетки
            p.setPen(QPen(edge_col, 1.8))
            p.drawLine(pts_left[s], pts_left[s + 1])
            p.drawLine(pts_right[s], pts_right[s + 1])

            grid_pen = QPen(QColor(255, 255, 255, int(55 * fade)), 1)
            p.setPen(grid_pen)
            p.drawLine(pts_center[s], pts_center[s + 1])
            p.drawLine(pts_q1[s], pts_q1[s + 1])
            p.drawLine(pts_q3[s], pts_q3[s + 1])

            if s in (7, 15):
                dist_label = "50m" if s == 7 else "100m"
                p.setPen(QPen(QColor(255, 255, 255, 160), 1.2, Qt.PenStyle.DashLine))
                p.drawLine(pts_left[s], pts_right[s])
                p.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
                p.setPen(QColor(210, 230, 245, 200))
                p.drawText(int(pts_right[s].x() + 6), int(pts_right[s].y() + 4), dist_label)


# ================= 5. ЧИТАЕМЫЙ НЕОНОВЫЙ ГРАФИК ТЕЛЕМЕТРИИ =================
class TelemetryGraphWidget(DraggableHUDWidget):
    def paintEvent(self, event):
        p = self.begin_hud_painter()
        self.draw_card_bg(p, 215)
        w, h = self.width(), self.height()

        # Шапка графика + легенда
        p.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
        p.setPen(QColor(200, 220, 235))
        p.drawText(14, 7, w - 28, 18, Qt.AlignmentFlag.AlignLeft,
                   f"LIVE vs BENCHMARK   |   {self.state.speed_kmh:.0f} км/ч")

        p.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        p.setPen(QColor(60, 255, 120))
        p.drawText(w - 185, 7, 55, 18, Qt.AlignmentFlag.AlignRight, "● ГАЗ")
        p.setPen(QColor(255, 75, 85))
        p.drawText(w - 120, 7, 65, 18, Qt.AlignmentFlag.AlignRight, "● ТОРМОЗ")
        p.setPen(QColor(180, 200, 220))
        p.drawText(w - 50, 7, 38, 18, Qt.AlignmentFlag.AlignRight, "╌ РЕФ")

        gx, gy = 12, 28
        gw, gh = w - 24, h - 38
        if self.bg_opacity_pct > 5:
            p.fillRect(gx, gy, gw, gh, QColor(6, 10, 15, int(195 * (self.bg_opacity_pct / 100.0))))

        # Тонкая координатная сетка
        p.setPen(QPen(QColor(120, 180, 210, 28), 1))
        for k in (0.25, 0.5, 0.75):
            yy = int(gy + gh * k)
            p.drawLine(gx, yy, gx + gw, yy)
        for kx in (0.2, 0.4, 0.6, 0.8):
            xx = int(gx + gw * kx)
            p.drawLine(xx, gy, xx, gy + gh)

        # Вертикальный курсор NOW (текущая позиция машины: слева история, справа — что ждёт впереди)
        now_x = gx + int(gw * 0.40)
        cx = self.state.lap_pct
        win_back = 0.06   # 6% круга позади (крупный масштаб, чтобы кривые не сливались!)
        win_ahead = 0.09  # 9% круга впереди
        total_span = win_back + win_ahead
        base_y = gy + gh - 3

        def rel_pct(pt_pct):
            d = pt_pct - cx
            if d > 0.5:
                d -= 1.0
            elif d < -0.5:
                d += 1.0
            return d

        def to_pt(rel_d, val_0_1) -> QPointF:
            x = gx + ((rel_d + win_back) / total_span) * gw
            y = base_y - max(0.0, min(1.0, val_0_1)) * (gh - 8)
            return QPointF(x, y)

        def draw_filled_curve(points, line_col: QColor, fill_alpha: int, width: float, dashed=False):
            if len(points) < 2:
                return
            path = QPainterPath()
            path.moveTo(points[0])
            for i in range(1, len(points)):
                if abs(points[i].x() - points[i - 1].x()) < gw * 0.25:
                    path.lineTo(points[i])
                else:
                    path.moveTo(points[i])

            if fill_alpha > 0:
                fill_path = QPainterPath(path)
                fill_path.lineTo(points[-1].x(), base_y)
                fill_path.lineTo(points[0].x(), base_y)
                fill_path.closeSubpath()
                grad = QLinearGradient(0, gy, 0, base_y)
                grad.setColorAt(0.0, QColor(line_col.red(), line_col.green(), line_col.blue(), fill_alpha))
                grad.setColorAt(1.0, QColor(line_col.red(), line_col.green(), line_col.blue(), 5))
                p.fillPath(fill_path, QBrush(grad))

            pen_style = Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine
            if not dashed:
                p.setPen(QPen(QColor(line_col.red(), line_col.green(), line_col.blue(), 70), width + 3.0))
                p.drawPath(path)
            p.setPen(QPen(line_col, width, pen_style))
            p.drawPath(path)

        # 1. Кривые ЭТАЛОНА с мягкой заливкой по всей ширине окна
        ref = self.state.ref_data
        if ref:
            raw_thr, raw_brk, raw_cl = [], [], []
            for r in ref:
                rd = rel_pct(r["LapDistPct"])
                if -win_back <= rd <= win_ahead:
                    raw_thr.append((rd, r["Throttle"]))
                    raw_brk.append((rd, r["Brake"]))
                    if self.state.show_clutch:
                        raw_cl.append((rd, r.get("Clutch", 0.0)))

            raw_thr.sort(key=lambda item: item[0])
            raw_brk.sort(key=lambda item: item[0])

            pts_ref_thr = [to_pt(d, v) for d, v in raw_thr]
            pts_ref_brk = [to_pt(d, v) for d, v in raw_brk]

            draw_filled_curve(pts_ref_thr, QColor(50, 240, 110, 190), fill_alpha=45, width=1.8, dashed=True)
            draw_filled_curve(pts_ref_brk, QColor(255, 70, 80, 210), fill_alpha=55, width=1.8, dashed=True)

            if self.state.show_clutch and raw_cl:
                raw_cl.sort(key=lambda item: item[0])
                draw_filled_curve([to_pt(d, v) for d, v in raw_cl], QColor(0, 210, 255, 170), fill_alpha=25, width=1.5, dashed=True)

        # 2. Живой след ИГРОКА позади курсора NOW (яркие сплошные неоновые линии)
        trace = list(self.state.player_trace)
        if len(trace) > 2:
            p_thr, p_brk, p_cl = [], [], []
            for item in trace:
                rd = rel_pct(item[0])
                if -win_back <= rd <= 0.005:
                    p_thr.append((rd, item[1]))
                    p_brk.append((rd, item[2]))
                    if self.state.show_clutch:
                        p_cl.append((rd, item[3]))

            draw_filled_curve([to_pt(d, v) for d, v in p_thr], QColor(65, 255, 130), fill_alpha=0, width=2.6, dashed=False)
            draw_filled_curve([to_pt(d, v) for d, v in p_brk], QColor(255, 65, 75), fill_alpha=0, width=2.6, dashed=False)
            if self.state.show_clutch and p_cl:
                draw_filled_curve([to_pt(d, v) for d, v in p_cl], QColor(0, 220, 255), fill_alpha=0, width=2.2, dashed=False)

        # Курсор NOW поверх графиков
        p.setPen(QPen(QColor(0, 230, 255, 70), 5))
        p.drawLine(now_x, gy, now_x, gy + gh)
        p.setPen(QPen(QColor(220, 245, 255, 210), 1.5))
        p.drawLine(now_x, gy, now_x, gy + gh)


# ================= ГЛАВНОЕ ОКНО УПРАВЛЕНИЯ (CONTROL PANEL) =================
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
        self._binding_target = None  # "lock" или "hide"

        opts = self.layout_cfg.get("_options", {})
        self.state.show_clutch = bool(opts.get("show_clutch", False))
        self.state.warn_lead_sec = float(opts.get("warn_lead_sec", 5.0))
        self.lock_vk = int(opts.get("lock_vk", 0x77))  # F8 по умолчанию
        self.hide_vk = int(opts.get("hide_vk", 0x78))  # F9 по умолчанию

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

        # 1. Статус
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

        # 2. Основные кнопки
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

        # 3. Бинды горячих клавиш
        bind_row = QHBoxLayout()
        self.btn_bind_lock = QPushButton()
        self.btn_bind_lock.clicked.connect(lambda: self._start_rebinding("lock"))
        self.btn_bind_hide = QPushButton()
        self.btn_bind_hide.clicked.connect(lambda: self._start_rebinding("hide"))
        bind_row.addWidget(self.btn_bind_lock)
        bind_row.addWidget(self.btn_bind_hide)
        root.addLayout(bind_row)
        self._update_hotkey_labels()

        # 4. Опции тренера
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

        # 5. Виджеты и прозрачность
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

        hint = QLabel("💡 Чтобы изменить размер любого виджета — потяни мышкой за его правый нижний уголок (◢), когда виджеты разблокированы.")
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

        # Круг, оставшиеся круги и позиция в гонке
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