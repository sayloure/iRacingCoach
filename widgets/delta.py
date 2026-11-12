from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPen, QFont

from config import FONT_UI, FONT_MONO
from .base import DraggableHUDWidget


def format_lap_time_ms(sec: float) -> str:
    """Форматирует время круга с точностью до тысячных (3 знака после запятой): M:SS.mmm или SS.mmms."""
    if sec <= 0.1:
        return "--:--.---"
    mins = int(sec // 60)
    rem = sec - mins * 60
    return f"{mins}:{rem:06.3f}" if mins > 0 else f"{rem:06.3f}s"


class DeltaWidget(DraggableHUDWidget):
    def paintEvent(self, event):
        p = self.begin_hud_painter()
        self.draw_card_bg(p, 215)
        w, h = self.width(), self.height()

        # 1. Верхняя шапка: заголовок слева, позиция справа
        p.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
        p.setPen(QColor(150, 175, 195))
        p.drawText(14, 8, w - 28, 18, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "DELTA TIME vs REF")

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
        p.drawText(14, 8, w - 28, 18, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"POS {pos_str}")

        # 2. Крупная цифра Дельты
        d = self.state.delta_sec
        valid = getattr(self.state, "delta_valid", False) and self.state.connected

        if not valid or abs(d) > 60.0:
            txt = "Δ 0.00 s"
            col = QColor(175, 185, 195)
            status_tag = "ОЖИДАНИЕ"
        else:
            txt = f"Δ {d:+.2f} s"
            if d < -0.02:
                col = QColor(55, 255, 125)
                status_tag = "БЫСТРЕЕ"
            elif d > 0.02:
                col = QColor(255, 75, 80)
                status_tag = "МЕДЛЕННЕЕ"
            else:
                col = QColor(220, 235, 250)
                status_tag = "В ТЕМПЕ"

        main_fs = max(14, min(26, int(h * 0.15), int(w * 0.095)))
        p.setFont(QFont(FONT_MONO, main_fs, QFont.Weight.Bold))
        p.setPen(col)
        p.drawText(14, 24, w - 28, int(h * 0.23), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, txt)

        # 3. Строка скорости слева + статус справа
        ds = self.state.delta_speed
        spd_y = int(h * 0.45)
        p.setFont(QFont(FONT_UI, max(8, int(h * 0.062))))
        spd_col = QColor(55, 255, 125) if ds >= 1.0 else (QColor(255, 110, 95) if ds <= -1.0 else QColor(210, 220, 230))
        p.setPen(spd_col)
        p.drawText(14, spd_y, w - 28, 18, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"Скорость: {ds:+.0f} км/ч")

        p.setFont(QFont(FONT_UI, max(7, int(h * 0.056)), QFont.Weight.Bold))
        p.setPen(col)
        p.drawText(14, spd_y, w - 28, 18, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, status_tag)

        # 4. Разделитель + КРУГ и ТОЧНОЕ ВРЕМЯ КРУГА ЭТАЛОНА ДО ТЫСЯЧНЫХ (0.000)
        line_y = spd_y + 20
        p.setPen(QPen(QColor(180, 220, 240, 110), 1.2))
        p.drawLine(14, line_y, w - 14, line_y)

        p.setFont(QFont(FONT_MONO, max(8, int(h * 0.058)), QFont.Weight.Bold))
        p.setPen(QColor(235, 245, 255))
        p.drawText(14, line_y + 3, w - 28, 18, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"КР: {cur_lap}/{tot_str} ({rem_str})")

        ref_t_str = format_lap_time_ms(getattr(self.state, "ref_lap_time", 0.0))
        p.setPen(QColor(255, 200, 85))
        p.drawText(14, line_y + 3, w - 28, 18, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   f"РЕФ: {ref_t_str}")

        # 5. Мини-график истории дельты
        gx, gy = 14, line_y + 24
        gw, gh = w - 28, h - gy - 8
        if gh > 12:
            if self.bg_opacity_pct > 10:
                p.fillRect(gx, gy, gw, gh, QColor(6, 10, 14, int(140 * (self.bg_opacity_pct / 100.0))))
            mid_y = gy + gh // 2
            p.setPen(QPen(QColor(255, 255, 255, 40), 1, Qt.PenStyle.DashLine))
            p.drawLine(gx, mid_y, gx + gw, mid_y)

            hist = self.state.delta_history
            n_h = len(hist)
            if n_h > 2:
                p.setPen(QPen(col, 2))
                step = max(1, n_h // 40)
                for i in range(0, n_h - step, step):
                    x1 = gx + int(i / max(1, n_h - 1) * gw)
                    x2 = gx + int((i + step) / max(1, n_h - 1) * gw)
                    v1 = max(-3.0, min(3.0, hist[i]))
                    v2 = max(-3.0, min(3.0, hist[i + step]))
                    y1 = int(mid_y - (v1 / 3.0) * (gh * 0.42))
                    y2 = int(mid_y - (v2 / 3.0) * (gh * 0.42))
                    p.drawLine(x1, y1, x2, y2)