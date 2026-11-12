"""
Запусти iRacing, выезжай на трассу, затем выполни: python overlay.py
"""

import sys
import os
import math

from PyQt6.QtWidgets import QApplication, QWidget, QPushButton, QVBoxLayout, QHBoxLayout
from PyQt6.QtCore import Qt, QTimer, QPoint, QRect
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QBrush, QPainterPath

import irsdk
from reference import load_garage61_csv, get_target_state


# Цвета зон и разделителей
BG_MAIN = QColor(20, 20, 25, 220)
BG_GRAPH = QColor(25, 25, 30)
SEP = QPen(QColor(60, 60, 70), 1)
COLOR_THROTTLE = QColor(60, 255, 60)
COLOR_BRAKE = QColor(255, 60, 60)
COLOR_SPEED = Qt.GlobalColor.white
COLOR_SPEED_TARGET = QColor(160, 160, 160)

FONT_MONO = "Consolas"
FONT_UI = "Segoe UI"

W = 700
H = 500


class OverlayWindow(QWidget):
    def __init__(self):
        super().__init__()
        self._drag_wpos = QPoint()
        self._drag_mpos = QPoint()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(W, H)

        # Кнопка закрытия
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        tr = QHBoxLayout()
        tr.addStretch()
        btn = QPushButton("X")
        btn.setFixedSize(24, 24)
        btn.setStyleSheet(
            "QPushButton { background: #dc3545; color: white; border: none; "
            "border-radius: 12px; font-weight: bold; font-size: 12px; }"
            "QPushButton:hover { background: #ff5a67; }"
        )
        btn.clicked.connect(self.close_app)
        tr.addWidget(btn)
        layout.addLayout(tr)
        layout.addStretch()

        # Состояние
        self.lap = 0.0
        self.speed = 0.0
        self.brake = 0.0
        self.throttle = 0.0
        self.steer = 0.0
        self.t_speed = 0.0
        self.t_brake = 0.0
        self.t_throttle = 0.0
        self.t_steer = 0.0
        self.delta = 0.0
        self.connected = False
        self.track_len = 5000.0
        self.warn = ""
        self.warn_bg = QColor(0, 0, 0, 0)
        self.has_ref_steer = False
        self.ref = None

        csv_path = os.path.join(os.path.dirname(__file__), 'reference.csv')
        if os.path.exists(csv_path):
            self.ref = load_garage61_csv(csv_path)
            if self.ref:
                vals = [abs(r['SteeringWheelAngle']) for r in self.ref]
                self.has_ref_steer = max(vals) > 0.001
        else:
            print(f"Предупреждение: reference.csv не найден — {csv_path}")

        self.irc = irsdk.IRSDK()
        self.irc.startup()
        self.track_len_read = False

        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(33)

    def close_app(self):
        self.irc.shutdown()
        QApplication.instance().quit()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_wpos = self.pos()
            self._drag_mpos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            self.move(self._drag_wpos + event.globalPosition().toPoint() - self._drag_mpos)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_wpos = QPoint()
            self._drag_mpos = QPoint()

    # ----- Предупреждения -----
    def _update_warnings(self):
        self.warn = ""
        self.warn_bg = QColor(0, 0, 0, 0)
        if not self.connected or self.ref is None:
            return

        best_d = None
        for pt in self.ref:
            if pt['Brake'] > 0.5:
                d = pt['LapDistPct'] - self.lap
                if d < 0:
                    d += 1.0
                if d > 0 and (best_d is None or d < best_d):
                    best_d = d

        if best_d is not None and best_d > 0.01:
            dist_m = best_d * self.track_len
            speed_ms = self.speed / 3.6
            t = dist_m / speed_ms if speed_ms > 1.0 else 99.0

            if t < 1.0:
                self.warn = "🔴 ТОРМОЗИ!"
                self.warn_bg = QColor(220, 53, 69, 240)
            elif t < 3.0:
                self.warn = f"⚠️ Торможение: {t:.1f} сек"
                self.warn_bg = QColor(255, 152, 0, 220)
            elif t < 5.0:
                self.warn = f"⚠️ Готовься: {t:.1f} сек"
                self.warn_bg = QColor(255, 193, 7, 200)
        else:
            if self.t_brake > 0.5 and abs(self.brake - self.t_brake) < 0.15:
                self.warn = "✓ OK"
                self.warn_bg = QColor(40, 167, 69, 200)
            elif self.t_brake < 0.3 and self.t_throttle > 0.9 and self.throttle > 0.85:
                self.warn = "🟢 ГАЗ!"
                self.warn_bg = QColor(40, 167, 69, 200)
            elif self.t_brake > 0.5 and self.brake < 0.1:
                self.warn = "🔴 ТОРМОЗИ!"
                self.warn_bg = QColor(220, 53, 69, 240)

    # ----- Таймер -----
    def tick(self):
        self.connected = self.irc.is_connected
        if not self.connected:
            self.lap = self.speed = self.brake = self.throttle = self.steer = 0
            self.t_speed = self.t_brake = self.t_throttle = self.t_steer = 0
            self.delta = 0
            self.warn = ""
            self.warn_bg = QColor(0, 0, 0, 0)
            self.update()
            return

        lap_pct = self.irc['LapDistPct']
        if lap_pct is None:
            return

        if not self.track_len_read:
            tl = self.irc['TrackLength']
            if tl is not None and tl > 0:
                self.track_len = float(tl)
                self.track_len_read = True

        self.lap = lap_pct
        self.speed = self.irc['Speed']
        self.brake = self.irc['Brake']
        self.throttle = self.irc['Throttle']
        steer_raw = self.irc['SteeringWheelAngle']
        self.steer = steer_raw if steer_raw is not None else 0.0

        if self.ref:
            t = get_target_state(self.lap, self.ref)
            self.t_speed = t['Speed']
            self.t_brake = t['Brake']
            self.t_throttle = t['Throttle']
            self.t_steer = t['SteeringWheelAngle']
        else:
            self.t_speed = self.t_brake = self.t_throttle = self.t_steer = 0

        self.delta = self.speed - self.t_speed
        self._update_warnings()
        self.update()

    # ----- Отрисовка -----
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        # Общий фон
        p.fillRect(0, 0, w, h, BG_MAIN)

        if not self.connected:
            p.setPen(QColor(255, 200, 0))
            p.setFont(QFont(FONT_UI, 28, QFont.Weight.Bold))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Ожидание iRacing...")
            return

        ZONE_G = 0
        ZONE_G_H = 200
        ZONE_W_Y = ZONE_G + ZONE_G_H
        ZONE_W_H = 35
        ZONE_I_Y = ZONE_W_Y + ZONE_W_H
        ZONE_I_H = 120
        ZONE_T_Y = ZONE_I_Y + ZONE_I_H
        ZONE_T_H = h - ZONE_T_Y

        self._draw_graph_zone(p, ZONE_G, ZONE_G_H)
        self._draw_warn_zone(p, ZONE_W_Y, ZONE_W_H)
        self._draw_instruments_zone(p, ZONE_I_Y, ZONE_I_H)
        self._draw_telemetry_zone(p, ZONE_T_Y, ZONE_T_H)

        # Разделители
        p.setPen(SEP)
        p.drawLine(0, ZONE_W_Y, w, ZONE_W_Y)
        p.drawLine(0, ZONE_I_Y, w, ZONE_I_Y)
        p.drawLine(0, ZONE_T_Y, w, ZONE_T_Y)

    # ========== 1. ГРАФИК ==========
    def _draw_graph_zone(self, p, y0, zh):
        margin = 8
        gw = self.width() - 2 * margin
        cx = self.lap
        x0 = cx - 0.15
        x1 = cx + 0.15

        p.fillRect(margin, y0 + 4, gw, zh - 8, BG_GRAPH)

        def sx(v):
            return int(margin + (v - x0) / (x1 - x0) * gw)

        def sy(v):
            return int(y0 + 4 + zh - 8 - v * (zh - 8))

        # Оси
        p.setPen(QPen(QColor(80, 80, 90), 1))
        p.drawLine(margin, sy(0), margin + gw, sy(0))
        p.drawLine(margin, sy(1), margin + gw, sy(1))

        # Кривые
        if self.ref:
            p.setPen(QPen(COLOR_THROTTLE, 1))
            for i in range(len(self.ref) - 1):
                a, b = self.ref[i], self.ref[i + 1]
                p.drawLine(sx(a['LapDistPct']), sy(a['Throttle']),
                           sx(b['LapDistPct']), sy(b['Throttle']))

            p.setPen(QPen(COLOR_BRAKE, 1))
            for i in range(len(self.ref) - 1):
                a, b = self.ref[i], self.ref[i + 1]
                p.drawLine(sx(a['LapDistPct']), sy(a['Brake']),
                           sx(b['LapDistPct']), sy(b['Brake']))

        # Вертикальная линия
        lx = sx(self.lap)
        p.setPen(QPen(QColor(255, 255, 255, 60), 1, Qt.PenStyle.DashLine))
        p.drawLine(lx, y0 + 4, lx, y0 + zh - 4)

        # Точки
        p.setBrush(QBrush(COLOR_THROTTLE))
        p.setPen(QPen(Qt.GlobalColor.white, 1))
        tx, ty = sx(self.lap), sy(self.throttle)
        p.drawEllipse(tx - 3, ty - 3, 6, 6)

        p.setBrush(QBrush(COLOR_BRAKE))
        bx, by = sx(self.lap), sy(self.brake)
        p.drawEllipse(bx - 3, by - 3, 6, 6)

        # Легенда
        p.setFont(QFont(FONT_UI, 9))
        leg_x = margin + 6
        leg_y = y0 + 10
        p.fillRect(leg_x, leg_y, 8, 8, COLOR_THROTTLE)
        p.setPen(COLOR_THROTTLE)
        p.drawText(leg_x + 12, leg_y, 80, 12, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "throttle")

        p.fillRect(leg_x, leg_y + 14, 8, 8, COLOR_BRAKE)
        p.setPen(COLOR_BRAKE)
        p.drawText(leg_x + 12, leg_y + 14, 80, 12, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "brake")

    # ========== 2. ПРЕДУПРЕЖДЕНИЕ ==========
    def _draw_warn_zone(self, p, y0, zh):
        if not self.warn:
            return
        bw = 400
        bh = 30
        bx = (self.width() - bw) // 2
        by = y0 + (zh - bh) // 2

        path = QPainterPath()
        path.addRoundedRect(bx, by, bw, bh, 6, 6)
        p.fillPath(path, self.warn_bg)
        p.setPen(Qt.GlobalColor.white)
        p.setFont(QFont(FONT_UI, 14, QFont.Weight.Bold))
        p.drawText(bx, by, bw, bh, Qt.AlignmentFlag.AlignCenter, self.warn)

    # ========== 3. ИНДИКАТОРЫ ==========
    def _draw_instruments_zone(self, p, y0, zh):
        # Левая часть: руль
        steer_w = 140
        steer_h = zh - 10
        steer_x = 12
        steer_y = y0 + (zh - steer_h) // 2

        p.fillRect(steer_x, steer_y, steer_w, steer_h, QColor(0, 0, 0, 60))

        center_x = steer_x + steer_w // 2
        center_y = steer_y + steer_h // 2 - 5

        if self.has_ref_steer:
            # REF дуга (слева)
            ref_arc_w = 50
            ref_arc_h = 25
            ref_arc_x = steer_x + (steer_w // 2 - ref_arc_w) // 2 - 8
            ref_arc_y = center_y - ref_arc_h

            ref_rect = QRect(ref_arc_x, ref_arc_y, ref_arc_w, ref_arc_h)
            p.setPen(QPen(QColor(80, 80, 90), 3))
            p.drawArc(ref_rect, 180 * 16, 180 * 16)

            ref_deg = math.degrees(self.t_steer)
            p.setPen(QPen(QColor(160, 160, 160), 3))
            ref_a = int(((270 + ref_deg) % 360) * 16)
            p.drawArc(ref_rect, ref_a, 4 * 16)

            p.setPen(QColor(160, 160, 160))
            p.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
            p.drawText(ref_arc_x, ref_arc_y + ref_arc_h + 4, ref_arc_w, 14,
                       Qt.AlignmentFlag.AlignCenter, "REF")

            # YOU дуга (справа)
            you_arc_w = 50
            you_arc_h = 25
            you_arc_x = steer_x + steer_w // 2 + 8
            you_arc_y = center_y - you_arc_h

            you_rect = QRect(you_arc_x, you_arc_y, you_arc_w, you_arc_h)
            p.setPen(QPen(QColor(80, 80, 90), 3))
            p.drawArc(you_rect, 180 * 16, 180 * 16)

            cur_deg = math.degrees(self.steer)
            diff = abs(cur_deg - ref_deg)
            if diff < 5:
                sc = QColor(60, 255, 60)
            elif diff < 15:
                sc = QColor(255, 220, 40)
            else:
                sc = QColor(255, 60, 60)

            p.setPen(QPen(sc, 3))
            you_a = int(((270 + cur_deg) % 360) * 16)
            p.drawArc(you_rect, you_a, 4 * 16)

            p.setPen(sc)
            p.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
            p.drawText(you_arc_x, you_arc_y + you_arc_h + 4, you_arc_w, 14,
                       Qt.AlignmentFlag.AlignCenter, "YOU")

            # Число между дугами
            p.setFont(QFont(FONT_MONO, 13, QFont.Weight.Bold))
            p.setPen(QColor(200, 200, 220))
            p.drawText(steer_x, steer_y + steer_h - 22, steer_w, 18,
                       Qt.AlignmentFlag.AlignCenter,
                       f"{cur_deg:.0f}° / {ref_deg:.0f}°")
        else:
            # Только YOU дуга
            arc_w = 55
            arc_h = 28
            arc_x = steer_x + (steer_w - arc_w) // 2
            arc_y = center_y - arc_h

            rect = QRect(arc_x, arc_y, arc_w, arc_h)
            p.setPen(QPen(QColor(80, 80, 90), 3))
            p.drawArc(rect, 180 * 16, 180 * 16)

            cur_deg = math.degrees(self.steer)
            p.setPen(QPen(QColor(60, 200, 255), 3))
            a = int(((270 + cur_deg) % 360) * 16)
            p.drawArc(rect, a, 4 * 16)

            p.setPen(QColor(60, 200, 255))
            p.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
            p.drawText(arc_x, arc_y + arc_h + 4, arc_w, 14,
                       Qt.AlignmentFlag.AlignCenter, "YOU")

            p.setFont(QFont(FONT_MONO, 11))
            p.setPen(QColor(200, 200, 220))
            p.drawText(steer_x, steer_y + steer_h - 22, steer_w, 18,
                       Qt.AlignmentFlag.AlignCenter, f"{cur_deg:.0f}°")

        # Правая часть: скорость
        speed_x = 200
        speed_y = y0 + (zh - 50) // 2

        p.setFont(QFont(FONT_MONO, 36, QFont.Weight.Bold))
        p.setPen(COLOR_SPEED)
        p.drawText(speed_x, speed_y, 160, 55, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{self.speed:.0f}")

        p.setFont(QFont(FONT_UI, 14))
        p.setPen(COLOR_SPEED_TARGET)
        p.drawText(speed_x + 150, speed_y + 5, 200, 40, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"/ {self.t_speed:.0f} km/h")

    # ========== 4. ТЕЛЕМЕТРИЯ ==========
    def _draw_telemetry_zone(self, p, y0, zh):
        cols = [
            ("BRAKE", lambda: f"{self.brake:.2f} / {self.t_brake:.2f}",
             COLOR_BRAKE if self.brake < self.t_brake * 0.8 and self.t_brake > 0.5 else QColor(60, 255, 60)),
            ("THROTTLE", lambda: f"{self.throttle:.2f} / {self.t_throttle:.2f}",
             QColor(60, 255, 60)),
            ("DELTA", lambda: f"{self.delta:+.1f} km/h",
             QColor(255, 60, 60) if self.delta > 5 else QColor(255, 200, 60) if self.delta > 0 else QColor(60, 255, 60)),
        ]

        cw = self.width() // 3
        label_font = QFont(FONT_UI, 9, QFont.Weight.Bold)
        val_font = QFont(FONT_MONO, 18, QFont.Weight.Bold)

        p.setPen(QColor(0, 0, 0, 60))
        p.drawLine(cw, y0 + 4, cw, y0 + zh - 4)
        p.drawLine(cw * 2, y0 + 4, cw * 2, y0 + zh - 4)

        for i, (label, val_fn, color) in enumerate(cols):
            cx = i * cw
            p.setFont(label_font)
            p.setPen(QColor(140, 140, 150))
            p.drawText(cx, y0 + 6, cw, 16, Qt.AlignmentFlag.AlignCenter, label)

            val = val_fn()
            p.setFont(val_font)
            p.setPen(color)
            p.drawText(cx, y0 + 22, cw, 50, Qt.AlignmentFlag.AlignCenter, val)

    def closeEvent(self, event):
        self.irc.shutdown()
        event.accept()


def main():
    app = QApplication(sys.argv)
    w = OverlayWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()