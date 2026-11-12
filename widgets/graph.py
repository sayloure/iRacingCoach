from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QColor, QPen, QFont, QBrush, QPainterPath, QLinearGradient

from config import FONT_UI
from .base import DraggableHUDWidget


class TelemetryGraphWidget(DraggableHUDWidget):
    def paintEvent(self, event):
        p = self.begin_hud_painter()
        self.draw_card_bg(p, 215)
        w, h = self.width(), self.height()

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

        p.setPen(QPen(QColor(120, 180, 210, 28), 1))
        for k in (0.25, 0.5, 0.75):
            yy = int(gy + gh * k)
            p.drawLine(gx, yy, gx + gw, yy)
        for kx in (0.2, 0.4, 0.6, 0.8):
            xx = int(gx + gw * kx)
            p.drawLine(xx, gy, xx, gy + gh)

        now_x = gx + int(gw * 0.40)
        cx = self.state.lap_pct
        win_back = 0.06
        win_ahead = 0.09
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

            draw_filled_curve([to_pt(d, v) for d, v in raw_thr], QColor(50, 240, 110, 190), fill_alpha=45, width=1.8, dashed=True)
            draw_filled_curve([to_pt(d, v) for d, v in raw_brk], QColor(255, 70, 80, 210), fill_alpha=55, width=1.8, dashed=True)

            if self.state.show_clutch and raw_cl:
                raw_cl.sort(key=lambda item: item[0])
                draw_filled_curve([to_pt(d, v) for d, v in raw_cl], QColor(0, 210, 255, 170), fill_alpha=25, width=1.5, dashed=True)

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

        p.setPen(QPen(QColor(0, 230, 255, 70), 5))
        p.drawLine(now_x, gy, now_x, gy + gh)
        p.setPen(QPen(QColor(220, 245, 255, 210), 1.5))
        p.drawLine(now_x, gy, now_x, gy + gh)