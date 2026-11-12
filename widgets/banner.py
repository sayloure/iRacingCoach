from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QPen, QFont, QPainterPath

from config import FONT_UI, FONT_MONO
from .base import DraggableHUDWidget


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
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "БАННЕР ТОРМОЖЕНИЯ (тяни за уголок ◢)")
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