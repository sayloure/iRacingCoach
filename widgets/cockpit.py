import math
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QBrush, QPolygonF

from config import FONT_UI, FONT_MONO
from .base import DraggableHUDWidget
from .pedals import draw_unified_pedal_bar


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

        # 1. Голографическая 3D-лента траектории с сеткой
        self._draw_trajectory_ribbon(p, center_left, center_w, int(h * 0.56))

        # 2. Неоновая дуга угла руля
        arc_w = min(int(center_w * 0.88), int(h * 0.78))
        arc_h = int(arc_w * 0.39)
        arc_x = center_left + (center_w - arc_w) // 2
        arc_y = int(h * 0.50)

        rect_outer = QRectF(arc_x, arc_y, arc_w, arc_h * 2)
        rect_inner = QRectF(arc_x + 9, arc_y + 9, arc_w - 18, (arc_h - 9) * 2)

        p.setPen(QPen(QColor(40, 60, 75, 160), 6))
        p.drawArc(rect_outer, 0 * 16, 180 * 16)
        p.setPen(QPen(QColor(35, 50, 65, 150), 4))
        p.drawArc(rect_inner, 0 * 16, 180 * 16)

        you_deg = -math.degrees(self.state.steer_rad)
        ref_deg = -math.degrees(self.state.t_steer_rad)

        def deg_to_qt_angle(deg_right_positive):
            clamped = max(-180.0, min(180.0, deg_right_positive))
            return int((90.0 - clamped * 0.45) * 16)

        cx_arc = arc_x + arc_w // 2
        p.setPen(QPen(QColor(255, 255, 255, 90), 5))
        p.drawLine(cx_arc, arc_y - 5, cx_arc, arc_y + 15)
        p.setPen(QPen(Qt.GlobalColor.white, 2))
        p.drawLine(cx_arc, arc_y - 5, cx_arc, arc_y + 15)

        ref_ang = deg_to_qt_angle(ref_deg)
        span_ref = ref_ang - (90 * 16)
        p.setPen(QPen(QColor(255, 150, 30, 70), 10))
        p.drawArc(rect_inner, 90 * 16, span_ref)
        p.setPen(QPen(QColor(255, 165, 45, 230), 4))
        p.drawArc(rect_inner, 90 * 16, span_ref)
        p.setPen(QPen(QColor(255, 220, 120), 7))
        p.drawArc(rect_inner, ref_ang - 5 * 16, 10 * 16)

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

        # 3. Блок Передачи и Скорости
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
        pts_q1, pts_q3 = [], []
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
            p.setPen(QPen(QColor(255, 255, 255, int(45 * fade)), 1))
            p.drawPolygon(poly)

            p.setPen(QPen(edge_col, 1.8))
            p.drawLine(pts_left[s], pts_left[s + 1])
            p.drawLine(pts_right[s], pts_right[s + 1])

            p.setPen(QPen(QColor(255, 255, 255, int(55 * fade)), 1))
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