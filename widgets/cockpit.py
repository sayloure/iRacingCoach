import math
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QBrush, QPolygonF, QPainterPath

from config import FONT_UI, FONT_MONO
from .base import DraggableHUDWidget
from .pedals import draw_unified_pedal_bar


class SteerGearRibbonWidget(DraggableHUDWidget):
    def __init__(self, key_name, title, default_rect, state, layout_cfg, default_bg_pct=0, min_size=(260, 180)):
        super().__init__(key_name, title, default_rect, state, layout_cfg, default_bg_pct, min_size)
        self._ribbon_rect = QRectF()
        self._dragging_ribbon = False
        self._rib_drag_start_mouse = None
        self._rib_drag_start_offset = (0, 0)
        self.on_internal_change = None

    def _in_ribbon_zone(self, local_pos) -> bool:
        return (
            not self.locked
            and self.state.cp_show_ribbon
            and not self._in_resize_zone(local_pos)
            and self._ribbon_rect.contains(QPointF(local_pos))
        )

    def mousePressEvent(self, event):
        if not self.locked and event.button() == Qt.MouseButton.LeftButton:
            local_pt = event.position().toPoint()
            if self._in_ribbon_zone(local_pt):
                self._dragging_ribbon = True
                self._rib_drag_start_mouse = event.globalPosition().toPoint()
                self._rib_drag_start_offset = (self.state.cp_ribbon_dx, self.state.cp_ribbon_dy)
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.locked:
            if self._dragging_ribbon and self._rib_drag_start_mouse is not None:
                delta = event.globalPosition().toPoint() - self._rib_drag_start_mouse
                self.state.cp_ribbon_dx = max(-220, min(220, self._rib_drag_start_offset[0] + delta.x()))
                self.state.cp_ribbon_dy = max(-150, min(150, self._rib_drag_start_offset[1] + delta.y()))
                self.update()
                return
            local_pt = event.position().toPoint()
            if self._in_ribbon_zone(local_pt):
                self.setCursor(Qt.CursorShape.SizeAllCursor)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging_ribbon:
            self._dragging_ribbon = False
            self._rib_drag_start_mouse = None
            if callable(self.on_internal_change):
                self.on_internal_change()
            return
        super().mouseReleaseEvent(event)

    def _set_elem_opacity(self, p: QPainter, elem_pct: int):
        base_op = self.fg_opacity_pct / 100.0
        p.setOpacity(base_op * (max(10, min(100, elem_pct)) / 100.0))

    def paintEvent(self, event):
        p = self.begin_hud_painter()
        self.draw_card_bg(p, 200)
        w, h = self.width(), self.height()

        pedal_top = int(h * 0.14)
        pedal_h = int(h * 0.76)

        # Левые педали
        left_pedals = []
        if self.state.cp_show_clutch:
            left_pedals.append(("СЦЕПЛ", self.state.clutch, self.state.t_clutch, QColor(0, 210, 255), self.state.cp_op_clutch))
        if self.state.cp_show_brake:
            left_pedals.append(("ТОРМ" if len(left_pedals) > 0 else "ТОРМОЗ",
                                self.state.brake, self.state.t_brake, QColor(255, 45, 60), self.state.cp_op_brake))

        if len(left_pedals) == 2:
            bw1 = max(18, int(w * 0.045))
            bw2 = max(20, int(w * 0.052))
            x_c1 = int(w * 0.055)
            x_c2 = int(w * 0.135)
            self._set_elem_opacity(p, left_pedals[0][4])
            draw_unified_pedal_bar(p, x_c1, pedal_top, bw1, pedal_h, left_pedals[0][1], left_pedals[0][2], left_pedals[0][0], left_pedals[0][3])
            self._set_elem_opacity(p, left_pedals[1][4])
            draw_unified_pedal_bar(p, x_c2, pedal_top, bw2, pedal_h, left_pedals[1][1], left_pedals[1][2], left_pedals[1][0], left_pedals[1][3])
            center_left = x_c2 + bw2 // 2 + 14
        elif len(left_pedals) == 1:
            bw = max(24, int(w * 0.065))
            x_b = int(w * 0.085)
            self._set_elem_opacity(p, left_pedals[0][4])
            draw_unified_pedal_bar(p, x_b, pedal_top, bw, pedal_h, left_pedals[0][1], left_pedals[0][2], left_pedals[0][0], left_pedals[0][3])
            center_left = x_b + bw // 2 + 16
        else:
            center_left = 18

        # Правая педаль (Газ)
        if self.state.cp_show_throttle:
            self._set_elem_opacity(p, self.state.cp_op_throttle)
            bw_thr = max(26, int(w * 0.068))
            x_t = w - int(w * 0.085)
            draw_unified_pedal_bar(
                p, x_center=x_t, top_y=pedal_top, bar_w=bw_thr, bar_h=pedal_h,
                you_val=self.state.throttle, ref_val=self.state.t_throttle,
                label="ГАЗ", main_col=QColor(40, 250, 110)
            )
            center_right = x_t - bw_thr // 2 - 16
        else:
            center_right = w - 18

        center_w = max(140, center_right - center_left)

        # 1. Голографическая 3D-лента траектории
        gear_mode = self.state.cp_gear_mode
        has_bottom_ui = (
            self.state.cp_show_steer
            or gear_mode != "off"
            or self.state.cp_show_speed_you
            or self.state.cp_show_speed_ref
        )
        ribbon_h = int(h * 0.56) if has_bottom_ui else int(h * 0.88)
        if self.state.cp_show_ribbon:
            self._draw_trajectory_ribbon(p, center_left, center_w, ribbon_h)

        # 2. Неоновая дуга угла руля
        arc_w = min(int(center_w * 0.72), int(h * 0.72))
        arc_h = int(arc_w * 0.38)
        arc_x = center_left + (center_w - arc_w) // 2
        arc_y = int(h * 0.51)

        if self.state.cp_show_steer:
            self._set_elem_opacity(p, self.state.cp_op_steer)
            p.setBrush(Qt.BrushStyle.NoBrush)
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

            if self.state.cp_show_steer_text:
                lbl_y = arc_y + int(arc_h * 0.42)
                left_box_x = max(center_left - 4, arc_x - 74)
                right_box_x = min(center_left + center_w - 72, arc_x + arc_w - 2)

                p.setFont(QFont(FONT_UI, 7, QFont.Weight.Bold))
                p.setPen(QColor(255, 110, 95) if diff >= 14 else you_col)
                p.drawText(left_box_x, lbl_y, 74, 14, Qt.AlignmentFlag.AlignRight, "ТЫ (РУЛЬ)")
                p.setFont(QFont(FONT_MONO, 13, QFont.Weight.Bold))
                p.drawText(left_box_x, lbl_y + 13, 74, 22, Qt.AlignmentFlag.AlignRight, f"{you_deg:+.0f}°")

                p.setFont(QFont(FONT_UI, 7, QFont.Weight.Bold))
                p.setPen(QColor(255, 180, 70))
                p.drawText(right_box_x, lbl_y, 74, 14, Qt.AlignmentFlag.AlignLeft, "ЭТАЛОН")
                p.setFont(QFont(FONT_MONO, 13, QFont.Weight.Bold))
                p.drawText(right_box_x, lbl_y + 13, 74, 22, Qt.AlignmentFlag.AlignLeft, f"{ref_deg:+.0f}°")

        # 3. Блок Передачи ("mismatch" = только при неверной передаче; "always" = всегда)
        gear_y = int(h * 0.65)
        g_you = self.state.gear
        g_ref = self.state.t_gear
        g_you_str = "R" if g_you == -1 else "N" if g_you == 0 else str(g_you)
        g_ref_str = str(g_ref) if g_ref > 0 else "-"
        mismatch = (g_ref > 0 and g_you != g_ref)

        show_gear_now = (
            (gear_mode == "mismatch" and mismatch)
            or (gear_mode == "always" and self.state.connected)
        )

        if show_gear_now:
            self._set_elem_opacity(p, self.state.cp_op_gear)
            box_h = max(38, int(h * 0.135))
            box_w = min(195, int(center_w * 0.62)) if mismatch else max(54, int(box_h * 1.45))
            box_x = center_left + (center_w - box_w) // 2

            badge_path = QPainterPath()
            badge_path.addRoundedRect(QRectF(box_x, gear_y, box_w, box_h), 9, 9)

            if mismatch:
                is_downshift = (g_ref < g_you)
                arrow = "↓" if is_downshift else "↑"
                g_text = f"{g_you_str} → {g_ref_str} {arrow}"
                bg_col = QColor(45, 14, 16, 225) if is_downshift else QColor(12, 32, 48, 225)
                border_col = QColor(255, 95, 45) if is_downshift else QColor(0, 225, 255)
                text_col = QColor(255, 195, 55) if is_downshift else QColor(100, 255, 180)
                gear_fs = max(16, min(26, int(box_h * 0.52)))
            else:
                g_text = g_you_str
                bg_col = QColor(12, 20, 28, 175)
                border_col = QColor(70, 210, 245, 110)
                text_col = QColor(240, 248, 255)
                gear_fs = max(20, min(32, int(box_h * 0.62)))

            p.fillPath(badge_path, bg_col)
            p.setBrush(Qt.BrushStyle.NoBrush)
            if mismatch:
                p.setPen(QPen(QColor(border_col.red(), border_col.green(), border_col.blue(), 80), 4.5))
                p.drawPath(badge_path)
            p.setPen(QPen(border_col, 2.0 if mismatch else 1.4))
            p.drawPath(badge_path)

            p.setFont(QFont(FONT_MONO, gear_fs, QFont.Weight.Bold))
            p.setPen(text_col)
            p.drawText(box_x, gear_y, box_w, box_h, Qt.AlignmentFlag.AlignCenter, g_text)

        # 4. Блок Скорости
        show_you_spd = self.state.cp_show_speed_you
        show_ref_spd = self.state.cp_show_speed_ref
        if show_you_spd or show_ref_spd:
            self._set_elem_opacity(p, self.state.cp_op_speed)
            spd_y = gear_y + max(40, int(h * 0.145)) if gear_mode != "off" else gear_y + 8
            spd_fs = max(10, min(15, int(h * 0.042)))
            p.setFont(QFont(FONT_MONO, spd_fs, QFont.Weight.Bold))

            if show_you_spd and show_ref_spd:
                spd_txt = f"{self.state.speed_kmh:.0f} км/ч  (эталон: {self.state.t_speed:.0f})"
                spd_col = QColor(255, 200, 90) if abs(self.state.delta_speed) > 5 else QColor(185, 240, 215)
            elif show_you_spd:
                spd_txt = f"{self.state.speed_kmh:.0f} км/ч"
                spd_col = QColor(220, 240, 250)
            else:
                spd_txt = f"эталон: {self.state.t_speed:.0f} км/ч"
                spd_col = QColor(255, 195, 85)

            p.setPen(spd_col)
            p.drawText(center_left, spd_y, center_w, 22, Qt.AlignmentFlag.AlignCenter, spd_txt)

    def _find_player_input_at(self, target_pct: float):
        trace = self.state.player_trace
        if not trace:
            return self.state.throttle, self.state.brake
        best_thr, best_brk = self.state.throttle, self.state.brake
        best_d = 999.0
        for pct, thr, brk, _ in trace:
            raw = abs(target_pct - pct)
            d = 1.0 - raw if raw > 0.5 else raw
            if d < best_d:
                best_d = d
                best_thr, best_brk = thr, brk
        if best_d > 0.015:
            return 0.0, 0.0
        return best_thr, best_brk

    def _draw_trajectory_ribbon(self, p: QPainter, x_offset: int, w_zone: int, h_zone: int):
        ref = self.state.ref_data
        if not ref:
            return

        self._set_elem_opacity(p, self.state.cp_op_ribbon)

        dx = self.state.cp_ribbon_dx
        dy = self.state.cp_ribbon_dy

        self._ribbon_rect = QRectF(x_offset + dx + 10, 8 + dy, w_zone - 20, h_zone - 8)

        if not self.locked:
            p.setPen(QPen(QColor(255, 210, 60, 140), 1, Qt.PenStyle.DotLine))
            p.drawRoundedRect(self._ribbon_rect, 6, 6)
            p.setFont(QFont(FONT_UI, 7, QFont.Weight.Bold))
            p.setPen(QColor(255, 215, 80, 210))
            p.drawText(int(self._ribbon_rect.left() + 6), int(self._ribbon_rect.top() + 12), "✥ Траектория (двигай)")

        n = len(ref)
        idx0 = self.state.ref_idx
        steps = 26
        car_step = 5

        lookahead_pct = min(0.055, 155.0 / max(1000.0, self.state.track_len))
        step_stride = max(1, int((n * lookahead_pct) / (steps - car_step)))

        cx = x_offset + w_zone * 0.5 + dx
        bottom_y = h_zone - 2 + dy
        top_y = 12 + dy

        rel_x = [0.0] * steps
        slope = 0.0
        cur_x = 0.0
        max_offset = w_zone * 0.38
        for s in range(car_step + 1, steps):
            pt = ref[(idx0 + (s - car_step) * step_stride) % n]
            t_ahead = (s - car_step) / float(steps - car_step - 1)
            steer_right_deg = -math.degrees(pt["SteeringWheelAngle"])
            slope += (steer_right_deg / 90.0) * 3.6
            cur_x += slope * (0.35 + 0.65 * t_ahead)
            rel_x[s] = max(-max_offset, min(max_offset, cur_x))

        slope_back = 0.0
        cur_x_back = 0.0
        for s in range(car_step - 1, -1, -1):
            pt = ref[(idx0 + (s - car_step) * step_stride) % n]
            steer_right_deg = -math.degrees(pt["SteeringWheelAngle"])
            slope_back -= (steer_right_deg / 90.0) * 2.0
            cur_x_back += slope_back * 0.35
            rel_x[s] = max(-max_offset, min(max_offset, cur_x_back))

        pts_left, pts_right, pts_center = [], [], []
        pts_q1, pts_q3 = [], []
        seg_data = []

        for s in range(steps):
            pt = ref[(idx0 + (s - car_step) * step_stride) % n]
            t = s / float(steps - 1)
            persp_t = math.pow(t, 0.82)
            y = bottom_y - persp_t * (bottom_y - top_y)

            half_w = (1.0 - t * 0.76) * (w_zone * 0.22)
            x_mid = cx + rel_x[s]
            pts_left.append(QPointF(x_mid - half_w, y))
            pts_right.append(QPointF(x_mid + half_w, y))
            pts_center.append(QPointF(x_mid, y))
            pts_q1.append(QPointF(x_mid - half_w * 0.5, y))
            pts_q3.append(QPointF(x_mid + half_w * 0.5, y))

            seg_data.append((pt["Brake"], pt["Throttle"], t, half_w, pt["LapDistPct"]))

        for s in range(steps - 1):
            brk, thr, t, half_w, seg_pct = seg_data[s]
            next_brk, next_thr, _, next_half_w, _ = seg_data[s + 1]
            fade = 1.0 - t * 0.62
            if s < car_step:
                fade *= 0.72
            alpha = int(210 * fade)

            poly_road = QPolygonF([pts_left[s], pts_right[s], pts_right[s + 1], pts_left[s + 1]])
            p.setBrush(QBrush(QColor(14, 24, 34, int(alpha * 0.65))))
            p.setPen(QPen(QColor(120, 175, 210, int(45 * fade)), 1))
            p.drawPolygon(poly_road)

            if brk > 0.05 or next_brk > 0.05:
                ref_col = QColor(255, 40, 55, int(alpha * 0.92)) if max(brk, next_brk) > 0.30 else QColor(255, 155, 30, int(alpha * 0.88))
                edge_col = QColor(255, 110, 120, int(230 * fade))
                r_int0 = max(0.08, min(1.0, brk))
                r_int1 = max(0.08, min(1.0, next_brk))
            elif thr > 0.08 or next_thr > 0.08:
                ref_col = QColor(30, 225, 95, int(alpha * 0.82))
                edge_col = QColor(90, 255, 150, int(220 * fade))
                r_int0 = max(0.08, min(1.0, thr))
                r_int1 = max(0.08, min(1.0, next_thr))
            else:
                ref_col = QColor(0, 190, 245, int(alpha * 0.55))
                edge_col = QColor(80, 225, 255, int(190 * fade))
                r_int0 = 0.16
                r_int1 = 0.16

            rw0 = half_w * r_int0
            rw1 = next_half_w * r_int1
            c0, c1 = pts_center[s], pts_center[s + 1]
            poly_ref = QPolygonF([
                QPointF(c0.x() - rw0, c0.y()),
                QPointF(c0.x() + rw0, c0.y()),
                QPointF(c1.x() + rw1, c1.y()),
                QPointF(c1.x() - rw1, c1.y()),
            ])
            p.setBrush(QBrush(ref_col))
            p.setPen(QPen(QColor(255, 255, 255, int(65 * fade)), 1))
            p.drawPolygon(poly_ref)

            p.setPen(QPen(edge_col, 1.7))
            p.drawLine(pts_left[s], pts_left[s + 1])
            p.drawLine(pts_right[s], pts_right[s + 1])

            p.setPen(QPen(QColor(255, 255, 255, int(45 * fade)), 1))
            p.drawLine(pts_center[s], pts_center[s + 1])
            p.drawLine(pts_q1[s], pts_q1[s + 1])
            p.drawLine(pts_q3[s], pts_q3[s + 1])

            if self.state.cp_show_player_on_rib and s < car_step:
                u_thr, u_brk = self._find_player_input_at(seg_pct)
                u_val = u_brk if u_brk > 0.05 else (u_thr if u_thr > 0.05 else 0.0)
                if u_val > 0.05:
                    uw0 = half_w * min(1.0, u_val)
                    uw1 = next_half_w * min(1.0, u_val)
                    u_col = QColor(255, 220, 90, 120) if u_brk > 0.05 else QColor(120, 255, 190, 100)
                    u_poly = QPolygonF([
                        QPointF(c0.x() - uw0, c0.y()),
                        QPointF(c0.x() + uw0, c0.y()),
                        QPointF(c1.x() + uw1, c1.y()),
                        QPointF(c1.x() - uw1, c1.y()),
                    ])
                    p.setBrush(QBrush(u_col))
                    p.setPen(QPen(QColor(255, 255, 255, 160), 1, Qt.PenStyle.DotLine))
                    p.drawPolygon(u_poly)

            if s in (car_step + 6, car_step + 13):
                dist_label = "50m" if s == car_step + 6 else "100m"
                p.setPen(QPen(QColor(255, 255, 255, 160), 1.2, Qt.PenStyle.DashLine))
                p.drawLine(pts_left[s], pts_right[s])
                p.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
                p.setPen(QColor(210, 230, 245, 200))
                p.drawText(int(pts_right[s].x() + 6), int(pts_right[s].y() + 4), dist_label)

        if self.state.cp_show_player_on_rib:
            self._set_elem_opacity(p, self.state.cp_op_player_on_rib)
            pL = pts_left[car_step]
            pR = pts_right[car_step]
            pC = pts_center[car_step]
            hw = seg_data[car_step][3]

            ref_b, ref_t = self.state.t_brake, self.state.t_throttle
            you_b, you_t = self.state.brake, self.state.throttle

            if ref_b > 0.08 or you_b > 0.08:
                ref_active = ref_b
                you_active = you_b
                base_bar_col = QColor(255, 55, 65)
            else:
                ref_active = ref_t
                you_active = you_t
                base_bar_col = QColor(45, 245, 105)

            diff_pedal = abs(you_active - ref_active)
            is_matched = (
                self.state.cp_show_pedal_match
                and (ref_active > 0.08 or you_active > 0.08)
                and diff_pedal <= 0.10
            )

            line_L = QPointF(pL.x() - 8, pL.y())
            line_R = QPointF(pR.x() + 8, pR.y())

            if is_matched:
                glow_col = QColor(0, 255, 190, 150)
                main_line_col = QColor(120, 255, 220, 255)
            else:
                glow_col = QColor(base_bar_col.red(), base_bar_col.green(), base_bar_col.blue(), 95)
                main_line_col = QColor(255, 255, 255, 210)

            p.setPen(QPen(glow_col, 8))
            p.drawLine(line_L, line_R)
            p.setPen(QPen(main_line_col, 2.2))
            p.drawLine(line_L, line_R)

            if you_active > 0.02:
                uw = hw * min(1.0, you_active)
                fill_c = QColor(0, 255, 185) if is_matched else QColor(255, 225, 70) if (you_b > 0.05 or ref_b > 0.08) else QColor(70, 255, 130)
                p.setPen(QPen(fill_c, 5.0))
                p.drawLine(QPointF(pC.x() - uw, pC.y()), QPointF(pC.x() + uw, pC.y()))

                p.setPen(QPen(Qt.GlobalColor.white, 2.8))
                p.drawLine(QPointF(pC.x() - uw, pC.y() - 6), QPointF(pC.x() - uw, pC.y() + 6))
                p.drawLine(QPointF(pC.x() + uw, pC.y() - 6), QPointF(pC.x() + uw, pC.y() + 6))

            chev = QPolygonF([
                QPointF(pC.x(), pC.y() - 7),
                QPointF(pC.x() - 7, pC.y() + 4),
                QPointF(pC.x() + 7, pC.y() + 4),
            ])
            p.setBrush(QBrush(QColor(0, 255, 190) if is_matched else Qt.GlobalColor.white))
            p.setPen(QPen(QColor(10, 20, 30), 1.5))
            p.drawPolygon(chev)
            p.setBrush(Qt.BrushStyle.NoBrush)