import bisect
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
        usable_h = gh - 8

        def to_pt(rel_d: float, val_0_1: float) -> QPointF:
            x = gx + ((rel_d + win_back) / total_span) * gw
            v = 0.0 if val_0_1 < 0.0 else (1.0 if val_0_1 > 1.0 else val_0_1)
            y = base_y - v * usable_h
            return QPointF(x, y)

        def draw_curve(points, line_col: QColor, fill_alpha: int, width: float, dashed=False):
            if len(points) < 2:
                return
            path = QPainterPath()
            path.moveTo(points[0])
            max_jump = gw * 0.30
            for i in range(1, len(points)):
                if abs(points[i].x() - points[i - 1].x()) < max_jump:
                    path.lineTo(points[i])
                else:
                    path.moveTo(points[i])

            if fill_alpha > 0:
                fill_path = QPainterPath(path)
                fill_path.lineTo(points[-1].x(), base_y)
                fill_path.lineTo(points[0].x(), base_y)
                fill_path.closeSubpath()
                p.fillPath(fill_path, QColor(line_col.red(), line_col.green(), line_col.blue(), fill_alpha))

            pen_style = Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine
            p.setPen(QPen(line_col, width, pen_style))
            p.drawPath(path)

        # 1. СТАБИЛЬНЫЙ ЭТАЛОН БЕЗ ПУЛЬСАЦИИ (используем предвычисленный лёгкий массив ref_graph_pts)
        g_pts = self.state.ref_graph_pts
        g_pcts = self.state.ref_graph_pcts
        if g_pts and g_pcts:
            n = len(g_pts)
            low_p = cx - win_back
            high_p = cx + win_ahead

            # Собираем индексы точек в окне видимости (с учётом перехода через линию старт-финиш)
            if 0.0 <= low_p and high_p <= 1.0:
                i0 = max(0, bisect.bisect_left(g_pcts, low_p) - 1)
                i1 = min(n, bisect.bisect_right(g_pcts, high_p) + 1)
                indices = range(i0, i1)
            elif low_p < 0.0:
                i_tail = max(0, bisect.bisect_left(g_pcts, low_p + 1.0) - 1)
                i_head = min(n, bisect.bisect_right(g_pcts, high_p) + 1)
                indices = list(range(i_tail, n)) + list(range(0, i_head))
            else:
                i_tail = max(0, bisect.bisect_left(g_pcts, low_p) - 1)
                i_head = min(n, bisect.bisect_right(g_pcts, high_p - 1.0) + 1)
                indices = list(range(i_tail, n)) + list(range(0, i_head))

            pts_thr, pts_brk, pts_cl = [], [], []
            show_cl = self.state.show_clutch
            for idx in indices:
                pt_pct, thr, brk, cl = g_pts[idx]
                rd = pt_pct - cx
                if rd > 0.5:
                    rd -= 1.0
                elif rd < -0.5:
                    rd += 1.0
                if -win_back - 0.005 <= rd <= win_ahead + 0.005:
                    pts_thr.append(to_pt(rd, thr))
                    pts_brk.append(to_pt(rd, brk))
                    if show_cl:
                        pts_cl.append(to_pt(rd, cl))

            draw_curve(pts_thr, QColor(50, 240, 110, 195), fill_alpha=32, width=1.8, dashed=True)
            draw_curve(pts_brk, QColor(255, 70, 80, 215), fill_alpha=38, width=1.8, dashed=True)
            if show_cl and pts_cl:
                draw_curve(pts_cl, QColor(0, 210, 255, 175), fill_alpha=20, width=1.5, dashed=True)

        # 2. ЖИВОЙ СЛЕД ИГРОКА БЕЗ ДРОЖАНИЯ (рисуем все точки подряд без плавающего шага!)
        trace = self.state.player_trace
        if len(trace) > 1:
            p_thr, p_brk, p_cl = [], [], []
            show_cl = self.state.show_clutch
            for pt_pct, thr, brk, cl in trace:
                rd = pt_pct - cx
                if rd > 0.5:
                    rd -= 1.0
                elif rd < -0.5:
                    rd += 1.0
                if -win_back <= rd <= 0.003:
                    p_thr.append(to_pt(rd, thr))
                    p_brk.append(to_pt(rd, brk))
                    if show_cl:
                        p_cl.append(to_pt(rd, cl))

            draw_curve(p_thr, QColor(65, 255, 130), fill_alpha=0, width=2.4, dashed=False)
            draw_curve(p_brk, QColor(255, 65, 75), fill_alpha=0, width=2.4, dashed=False)
            if show_cl and p_cl:
                draw_curve(p_cl, QColor(0, 220, 255), fill_alpha=0, width=2.0, dashed=False)

        # Линия текущей позиции NOW
        p.setPen(QPen(QColor(0, 230, 255, 70), 4))
        p.drawLine(now_x, gy, now_x, gy + gh)
        p.setPen(QPen(QColor(220, 245, 255, 220), 1.5))
        p.drawLine(now_x, gy, now_x, gy + gh)