from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPen, QFont

from config import FONT_UI, FONT_MONO
from .base import DraggableHUDWidget


class DeltaWidget(DraggableHUDWidget):
    def paintEvent(self, event):
        p = self.begin_hud_painter()
        self.draw_card_bg(p, 215)
        w, h = self.width(), self.height()

        p.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
        p.setPen(QColor(150, 175, 195))
        p.drawText(14, 8, w - 28, 18, Qt.AlignmentFlag.AlignLeft, "DELTA TIME vs REF")

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

        ds = self.state.delta_speed
        p.setFont(QFont(FONT_UI, max(9, int(h * 0.075))))
        p.setPen(QColor(210, 220, 230))
        spd_y = int(h * 0.48)
        p.drawText(14, spd_y, w - 28, 20, Qt.AlignmentFlag.AlignLeft, f"Скорость: {ds:+.0f} км/ч")

        line_y = spd_y + 22
        p.setPen(QPen(QColor(180, 220, 240, 110), 1.2))
        p.drawLine(14, line_y, w - 14, line_y)

        p.setFont(QFont(FONT_MONO, max(9, int(h * 0.075)), QFont.Weight.Bold))
        p.setPen(QColor(235, 245, 255))
        p.drawText(14, line_y + 4, w - 28, 20, Qt.AlignmentFlag.AlignLeft,
                   f"КРУГ: {cur_lap}/{tot_str}  (ОСТ: {rem_str})")

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