import math
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QBrush, QPolygonF, QPainterPath

from config import FONT_UI, FONT_MONO
from .base import DraggableHUDWidget
from .pedals import draw_unified_pedal_bar

MODULE_TITLES = {
    "ribbon": "Траектория",
    "steer": "Дуга Руля",
    "steer_text": "Текст Руля",
    "gear": "Передача",
    "speed": "Скорость",
    "brake": "Тормоз",
    "throttle": "Газ",
    "clutch": "Сцепление",
}


class SteerGearRibbonWidget(DraggableHUDWidget):
    MOD_RESIZE_CORNER = 14

    def __init__(self, key_name, title, default_rect, state, layout_cfg, default_bg_pct=0, min_size=(340, 220)):
        super().__init__(key_name, title, default_rect, state, layout_cfg, default_bg_pct, min_size)
        self._module_rects = {}
        self._dragging_mod = None
        self._resizing_mod = None
        self._mod_drag_start_mouse = None
        self._mod_drag_start_offset = (0, 0)
        self._mod_resize_start_scale = 100
        self._hovered_mod = None
        self.on_internal_change = None

    def _get_offset(self, mod_key: str):
        pair = self.state.cp_offsets.get(mod_key, [0, 0])
        return int(pair[0]), int(pair[1])

    def _set_offset(self, mod_key: str, dx: int, dy: int):
        self.state.cp_offsets[mod_key] = [
            max(-450, min(450, int(dx))),
            max(-350, min(350, int(dy))),
        ]

    def _get_scale(self, mod_key: str) -> float:
        return max(40, min(250, int(self.state.cp_scales.get(mod_key, 100)))) / 100.0

    def _set_scale_pct(self, mod_key: str, pct: int):
        self.state.cp_scales[mod_key] = max(40, min(250, int(pct)))

    def _hit_test_module(self, local_pt) -> tuple[str | None, bool]:
        if self.locked or self._in_resize_zone(local_pt):
            return None, False
        pt_f = QPointF(local_pt)
        priority = ("steer_text", "gear", "speed", "steer", "brake", "throttle", "clutch", "ribbon")
        for k in priority:
            rect = self._module_rects.get(k)
            if rect is not None and rect.contains(pt_f):
                in_corner = (
                    pt_f.x() >= rect.right() - self.MOD_RESIZE_CORNER
                    and pt_f.y() >= rect.bottom() - self.MOD_RESIZE_CORNER
                )
                return k, in_corner
        return None, False

    def mousePressEvent(self, event):
        if not self.locked and event.button() == Qt.MouseButton.LeftButton:
            local_pt = event.position().toPoint()
            hit, in_corner = self._hit_test_module(local_pt)
            if hit is not None:
                self._mod_drag_start_mouse = event.globalPosition().toPoint()
                if in_corner:
                    self._resizing_mod = hit
                    self._mod_resize_start_scale = int(self.state.cp_scales.get(hit, 100))
                else:
                    self._dragging_mod = hit
                    self._mod_drag_start_offset = self._get_offset(hit)
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.locked:
            if self._resizing_mod is not None and self._mod_drag_start_mouse is not None:
                delta = event.globalPosition().toPoint() - self._mod_drag_start_mouse
                change = int((delta.x() + delta.y()) * 0.45)
                self._set_scale_pct(self._resizing_mod, self._mod_resize_start_scale + change)
                self.update()
                if callable(self.on_internal_change):
                    self.on_internal_change()
                return

            if self._dragging_mod is not None and self._mod_drag_start_mouse is not None:
                delta = event.globalPosition().toPoint() - self._mod_drag_start_mouse
                nx = self._mod_drag_start_offset[0] + delta.x()
                ny = self._mod_drag_start_offset[1] + delta.y()
                self._set_offset(self._dragging_mod, nx, ny)
                self.update()
                if callable(self.on_internal_change):
                    self.on_internal_change()
                return

            local_pt = event.position().toPoint()
            hit, in_corner = self._hit_test_module(local_pt)
            if hit != self._hovered_mod:
                self._hovered_mod = hit
                self.update()
            if hit is not None:
                self.setCursor(Qt.CursorShape.SizeFDiagCursor if in_corner else Qt.CursorShape.SizeAllCursor)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging_mod is not None or self._resizing_mod is not None:
            self._dragging_mod = None
            self._resizing_mod = None
            self._mod_drag_start_mouse = None
            if callable(self.on_internal_change):
                self.on_internal_change()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        if not self.locked:
            local_pt = event.position().toPoint()
            hit, _ = self._hit_test_module(local_pt)
            if hit is not None:
                step = 5 if event.angleDelta().y() > 0 else -5
                cur = int(self.state.cp_scales.get(hit, 100))
                self._set_scale_pct(hit, cur + step)
                self.update()
                if callable(self.on_internal_change):
                    self.on_internal_change()
                event.accept()
                return
        super().wheelEvent(event)

    def _set_elem_opacity(self, p: QPainter, elem_pct: int):
        base_op = self.fg_opacity_pct / 100.0
        p.setOpacity(base_op * (max(10, min(100, elem_pct)) / 100.0))

    def _draw_mod_edit_box(self, p: QPainter, mod_key: str, rect: QRectF):
        self._module_rects[mod_key] = rect
        if self.locked:
            return
        is_active = (self._hovered_mod == mod_key or self._dragging_mod == mod_key or self._resizing_mod == mod_key)
        col = QColor(0, 240, 255, 220) if is_active else QColor(140, 195, 220, 85)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(col, 1.3 if is_active else 1.0, Qt.PenStyle.DashLine))
        p.drawRoundedRect(rect, 6, 6)

        p.setPen(QPen(QColor(0, 240, 255, 235 if is_active else 140), 1.8))
        rx, ry = int(rect.right()), int(rect.bottom())
        for off in (3, 7, 11):
            p.drawLine(rx - off, ry - 2, rx - 2, ry - off)

        if is_active:
            sc_pct = int(self.state.cp_scales.get(mod_key, 100))
            p.setFont(QFont(FONT_UI, 7, QFont.Weight.Bold))
            p.setPen(QColor(0, 240, 255, 245))
            p.drawText(int(rect.left() + 5), int(rect.top() + 11), f"✥ {MODULE_TITLES.get(mod_key, mod_key)} ({sc_pct}%)")

    def paintEvent(self, event):
        p = self.begin_hud_painter()
        self.draw_card_bg(p, 200)
        w, h = self.width(), self.height()
        self._module_rects.clear()

        pedal_top_base = int(h * 0.13)
        pedal_h_base = int(h * 0.74)
        bw_base = max(24, min(36, int(w * 0.058)))

        sy = self.state.cp_pedal_show_you_pct
        sr = self.state.cp_pedal_show_ref_pct

        # 1. СЦЕПЛЕНИЕ
        if self.state.cp_show_clutch:
            self._set_elem_opacity(p, self.state.cp_op_clutch)
            dx, dy = self._get_offset("clutch")
            sc = self._get_scale("clutch")
            xc = int(w * 0.06) + dx
            ph = int(pedal_h_base * sc)
            yt = pedal_top_base + dy + (pedal_h_base - ph) // 2
            rect_c = draw_unified_pedal_bar(
                p, x_center=xc, top_y=yt, bar_w=max(16, int(bw_base * 0.85 * sc)), bar_h=ph,
                you_val=self.state.clutch, ref_val=self.state.t_clutch,
                top_label="CLUTCH", bottom_label="CLT", main_col=QColor(0, 210, 255),
                readout_side="right", show_you_pct=sy, show_ref_pct=sr
            )
            self._draw_mod_edit_box(p, "clutch", rect_c)

        # 2. ТОРМОЗ
        if self.state.cp_show_brake:
            self._set_elem_opacity(p, self.state.cp_op_brake)
            dx, dy = self._get_offset("brake")
            sc = self._get_scale("brake")
            xb_base = int(w * 0.18) if self.state.cp_show_clutch else int(w * 0.10)
            xb = xb_base + dx
            ph = int(pedal_h_base * sc)
            yt = pedal_top_base + dy + (pedal_h_base - ph) // 2
            rect_b = draw_unified_pedal_bar(
                p, x_center=xb, top_y=yt, bar_w=max(18, int(bw_base * sc)), bar_h=ph,
                you_val=self.state.brake, ref_val=self.state.t_brake,
                top_label="BRAKE", bottom_label="BRK", main_col=QColor(255, 42, 70),
                readout_side="right", show_you_pct=sy, show_ref_pct=sr
            )
            self._draw_mod_edit_box(p, "brake", rect_b)

        # 3. ГАЗ
        if self.state.cp_show_throttle:
            self._set_elem_opacity(p, self.state.cp_op_throttle)
            dx, dy = self._get_offset("throttle")
            sc = self._get_scale("throttle")
            xt = w - int(w * 0.10) + dx
            ph = int(pedal_h_base * sc)
            yt = pedal_top_base + dy + (pedal_h_base - ph) // 2
            rect_t = draw_unified_pedal_bar(
                p, x_center=xt, top_y=yt, bar_w=max(18, int(bw_base * sc)), bar_h=ph,
                you_val=self.state.throttle, ref_val=self.state.t_throttle,
                top_label="THROTTLE", bottom_label="THR", main_col=QColor(35, 245, 135),
                readout_side="left", show_you_pct=sy, show_ref_pct=sr
            )
            self._draw_mod_edit_box(p, "throttle", rect_t)

        center_left = int(w * 0.22)
        center_w = int(w * 0.56)

        # 4. 3D-ЛЕНТА ТРАЕКТОРИИ
        if self.state.cp_show_ribbon:
            self._draw_trajectory_ribbon(p, center_left, center_w, h)

        # 5. ТАКТИЧЕСКИЙ ЩИТОК ПЕРЕДАЧИ (GEAR)
        self._draw_tactical_gear(p, w, h)

        # 6. ДУГА РУЛЯ + ТЕКСТ РУЛЯ
        if self.state.cp_show_steer or self.state.cp_show_steer_text:
            self._draw_tactical_steer(p, w, h)

        # 7. БЛОК СКОРОСТИ
        self._draw_speed_readout(p, w, h)

    def _draw_tactical_gear(self, p: QPainter, w: int, h: int):
        gear_mode = self.state.cp_gear_mode
        if gear_mode == "off":
            return

        g_you = self.state.gear
        g_ref = self.state.t_gear
        g_you_str = "R" if g_you == -1 else "N" if g_you == 0 else str(g_you)
        g_ref_str = str(g_ref) if g_ref > 0 else "-"
        mismatch = (g_ref > 0 and g_you != g_ref)

        show_now = (
            (gear_mode == "mismatch" and mismatch)
            or (gear_mode == "always")
            or (not self.locked)
        )
        if not show_now:
            return

        self._set_elem_opacity(p, self.state.cp_op_gear)
        dx, dy = self._get_offset("gear")
        sc = self._get_scale("gear")

        box_w = int(68 * sc)
        box_h = int(66 * sc)
        bx = (w - box_w) // 2 + dx
        by = int(h * 0.46) + dy - (box_h - 66) // 2

        if mismatch:
            is_down = (g_ref < g_you)
            shift_txt = f"▼ SHIFT {g_ref_str}" if is_down else f"▲ SHIFT UP ({g_ref_str})"
            shift_col = QColor(255, 95, 55) if is_down else QColor(255, 185, 35)
            p.setFont(QFont(FONT_UI, max(7, int(9 * sc)), QFont.Weight.Bold))
            p.setPen(shift_col)
            p.drawText(bx - 50, by - int(22 * sc), box_w + 100, int(20 * sc), Qt.AlignmentFlag.AlignCenter, shift_txt)

        rect_g = QRectF(bx, by, box_w, box_h)
        g_path = QPainterPath()
        g_path.addRoundedRect(rect_g, 6 * sc, 6 * sc)
        bg_c = QColor(38, 20, 22, 195) if mismatch else QColor(14, 24, 34, 175)
        p.fillPath(g_path, bg_c)

        p.setBrush(Qt.BrushStyle.NoBrush)
        border_c = QColor(255, 145, 45, 160) if mismatch else QColor(130, 175, 205, 110)
        p.setPen(QPen(border_c, 1.2))
        p.drawPath(g_path)

        corner_c = QColor(255, 185, 55, 240) if mismatch else QColor(210, 235, 250, 220)
        p.setPen(QPen(corner_c, 2.2))
        cl = max(5, int(9 * sc))
        p.drawLine(bx - 1, by - 1, bx + cl, by - 1)
        p.drawLine(bx - 1, by - 1, bx - 1, by + cl)
        p.drawLine(bx + box_w - cl, by - 1, bx + box_w + 1, by - 1)
        p.drawLine(bx + box_w + 1, by - 1, bx + box_w + 1, by + cl)
        p.drawLine(bx - 1, by + box_h - cl, bx - 1, by + box_h + 1)
        p.drawLine(bx - 1, by + box_h + 1, bx + cl, by + box_h + 1)
        p.drawLine(bx + box_w + 1, by + box_h - cl, bx + box_w + 1, by + box_h + 1)
        p.drawLine(bx + box_w - cl, by + box_h + 1, bx + box_w + 1, by + box_h + 1)

        p.setFont(QFont(FONT_UI, max(6, int(8 * sc)), QFont.Weight.Bold))
        p.setPen(QColor(175, 195, 215))
        p.drawText(bx, by + int(5 * sc), box_w, int(15 * sc), Qt.AlignmentFlag.AlignCenter, "GEAR")

        p.setFont(QFont(FONT_MONO, max(12, int(24 * sc)), QFont.Weight.Bold))
        p.setPen(QColor(255, 220, 90) if mismatch else Qt.GlobalColor.white)
        p.drawText(bx, by + int(18 * sc), box_w, box_h - int(18 * sc), Qt.AlignmentFlag.AlignCenter, g_you_str)

        self._draw_mod_edit_box(p, "gear", QRectF(bx - 24, by - int(22 * sc), box_w + 48, box_h + int(26 * sc)))

    def _draw_tactical_steer(self, p: QPainter, w: int, h: int):
        """
        Расширенная прогрессивная дуга руля (по умолчанию ±220°, настраивается 90..360°):
        - Нелинейная кривая (gamma = 0.82) сохраняет высокую точность около 0° и вмещает шпильки до 220°+.
        - Промежуточные засечки на ±90° и индикация перекрута (◄ / ►), если угол вышел за предел шкалы.
        """
        self._set_elem_opacity(p, self.state.cp_op_steer)
        dx, dy = self._get_offset("steer")
        sc = self._get_scale("steer")

        cx = w // 2 + dx
        arc_top_y = int(h * 0.73) + dy
        rx = int(min(125, int(w * 0.22)) * sc)
        ry = int(rx * 0.50)

        you_deg = -math.degrees(self.state.steer_rad)
        ref_deg = -math.degrees(self.state.t_steer_rad)
        max_deg = float(max(90, min(360, self.state.cp_steer_max_deg)))

        if self.state.cp_show_steer:
            # Расширенный сектор дуги: от 15° до 165° (охватывает 150 градусов!)
            start_deg = 15
            span_deg = 150
            half_arc_span = 72.0  # Половина рабочего хода от 90° (12 часов) влево/вправо

            rect_band = QRectF(cx - rx, arc_top_y, rx * 2, ry * 2)
            p.setBrush(Qt.BrushStyle.NoBrush)

            band_thick = max(6, int(14 * sc))
            p.setPen(QPen(QColor(80, 120, 145, 105), band_thick, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            p.drawArc(rect_band, start_deg * 16, span_deg * 16)

            off_r = max(4, int(8 * sc))
            r_out = QRectF(cx - rx - off_r, arc_top_y - off_r, (rx + off_r) * 2, (ry + off_r) * 2)
            r_in = QRectF(cx - rx + off_r, arc_top_y + off_r, (rx - off_r) * 2, (ry - off_r) * 2)
            p.setPen(QPen(QColor(130, 175, 205, 140), 1.2))
            p.drawArc(r_out, start_deg * 16, span_deg * 16)
            p.drawArc(r_in, start_deg * 16, span_deg * 16)

            def angle_point(steer_d: float, radius_offset: float):
                # Прогрессивное (нелинейное) отображение: высокая чувствительность в центре и запас по краям
                norm = max(-1.0, min(1.0, steer_d / max_deg))
                curved = math.copysign(math.pow(abs(norm), 0.85), norm)
                rad = math.radians(90.0 - curved * half_arc_span)
                px = cx + (rx + radius_offset) * math.cos(rad)
                py = (arc_top_y + ry) - (ry + radius_offset) * math.sin(rad)
                return QPointF(px, py)

            # Ось центра (0°) + промежуточные ориентиры на ±90°
            p.setPen(QPen(QColor(180, 215, 235, 150), 1.3))
            p.drawLine(cx, arc_top_y - off_r, cx, arc_top_y + off_r + 1)

            if max_deg >= 135:
                p.setPen(QPen(QColor(140, 185, 210, 95), 1.1))
                for mark_deg in (-90.0, 90.0):
                    p1 = angle_point(mark_deg, -off_r)
                    p2 = angle_point(mark_deg, +off_r)
                    p.drawLine(p1, p2)

            tri_top = QPolygonF([
                QPointF(cx, arc_top_y - off_r - 1),
                QPointF(cx - 4 * sc, arc_top_y - off_r - 7 * sc),
                QPointF(cx + 4 * sc, arc_top_y - off_r - 7 * sc),
            ])
            p.setBrush(QBrush(QColor(170, 210, 235, 200)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(tri_top)

            # 1. Метка ЭТАЛОНА (REF)
            pt_ref_out = angle_point(ref_deg, +9 * sc)
            pt_ref_in = angle_point(ref_deg, -9 * sc)
            p.setPen(QPen(QColor(255, 185, 50, 230), max(2.2, 3.8 * sc), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(pt_ref_in, pt_ref_out)

            # 2. Метка ИГРОКА (YOU) — прямая неоновая игла
            pt_you_out = angle_point(you_deg, +11 * sc)
            pt_you_in = angle_point(you_deg, -11 * sc)
            diff = abs(you_deg - ref_deg)
            glow_c = QColor(0, 235, 255, 155) if diff < 14 else QColor(255, 70, 85, 165)

            p.setPen(QPen(glow_c, max(4.0, 7.0 * sc), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(pt_you_in, pt_you_out)
            p.setPen(QPen(Qt.GlobalColor.white, max(1.8, 2.8 * sc), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(pt_you_in, pt_you_out)

            # Если руль вывернут дальше предела шкалы — подсвечиваем предупреждающий маркер на краю
            if abs(you_deg) > max_deg or abs(ref_deg) > max_deg:
                over_right = (you_deg > max_deg or ref_deg > max_deg)
                edge_pt = angle_point(max_deg if over_right else -max_deg, 0)
                p.setFont(QFont(FONT_UI, max(8, int(10 * sc)), QFont.Weight.Bold))
                p.setPen(QColor(255, 90, 90))
                sym = "►" if over_right else "◄"
                off_x = int(8 * sc) if over_right else int(-20 * sc)
                p.drawText(int(edge_pt.x() + off_x), int(edge_pt.y() + 5), sym)

            self._draw_mod_edit_box(p, "steer", QRectF(cx - rx - 14, arc_top_y - 18, (rx + 14) * 2, ry + 24))

        # ОТДЕЛЬНО ПЕРЕТАСКИВАЕМЫЙ ТЕКСТ РУЛЯ
        if self.state.cp_show_steer_text:
            tdx, tdy = self._get_offset("steer_text")
            tsc = self._get_scale("steer_text")
            tx = cx + tdx
            ty = arc_top_y + int(34 * sc) + tdy

            steer_str = f"STEER {you_deg:+.0f}°   (REF {ref_deg:+.0f}°)"
            fs = max(7, int(9.5 * tsc))
            p.setFont(QFont(FONT_MONO, fs, QFont.Weight.Bold))
            txt_col = QColor(self.state.cp_steer_text_color)
            if not txt_col.isValid():
                txt_col = QColor(200, 225, 245)
            p.setPen(txt_col)

            tw = int(230 * tsc)
            th = int(22 * tsc)
            txt_rect = QRectF(tx - tw // 2, ty, tw, th)
            p.drawText(txt_rect, Qt.AlignmentFlag.AlignCenter, steer_str)
            self._draw_mod_edit_box(p, "steer_text", txt_rect)

    def _draw_speed_readout(self, p: QPainter, w: int, h: int):
        show_you = self.state.cp_show_speed_you
        show_ref = self.state.cp_show_speed_ref
        if not (show_you or show_ref):
            return

        self._set_elem_opacity(p, self.state.cp_op_speed)
        dx, dy = self._get_offset("speed")
        sc = self._get_scale("speed")
        cx = w // 2 + dx
        sy = int(h * 0.90) + dy

        if show_you and show_ref:
            spd_txt = f"SPD {self.state.speed_kmh:.0f} км/ч   |   REF {self.state.t_speed:.0f}"
        elif show_you:
            spd_txt = f"SPD {self.state.speed_kmh:.0f} км/ч"
        else:
            spd_txt = f"REF {self.state.t_speed:.0f} км/ч"

        fs = max(7, int(10 * sc))
        p.setFont(QFont(FONT_MONO, fs, QFont.Weight.Bold))
        p.setPen(QColor(255, 200, 90) if abs(self.state.delta_speed) > 6 else QColor(175, 225, 240))
        bw = int(240 * sc)
        bh = int(24 * sc)
        s_rect = QRectF(cx - bw // 2, sy, bw, bh)
        p.drawText(s_rect, Qt.AlignmentFlag.AlignCenter, spd_txt)
        self._draw_mod_edit_box(p, "speed", s_rect)

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

    def _draw_trajectory_ribbon(self, p: QPainter, x_offset: int, w_zone: int, h_full: int):
        ref = self.state.ref_data
        if not ref:
            return

        self._set_elem_opacity(p, self.state.cp_op_ribbon)
        dx, dy = self._get_offset("ribbon")
        sc = self._get_scale("ribbon")

        n = len(ref)
        idx0 = self.state.ref_idx
        steps = 28
        car_step = 6

        reach_factor = max(0.25, min(1.0, self.state.cp_ribbon_top_reach / 100.0))
        top_fade_strength = max(0.0, min(1.0, self.state.cp_ribbon_top_fade / 100.0))

        lookahead_pct = min(0.055, (160.0 * reach_factor) / max(1000.0, self.state.track_len))
        step_stride = max(1, int((n * lookahead_pct) / (steps - car_step)))

        cx = x_offset + w_zone * 0.5 + dx
        base_span_h = (int(h_full * 0.85) - int(h_full * 0.14)) * sc
        bottom_y = int(h_full * 0.85) + dy
        top_y = int(bottom_y - base_span_h * reach_factor)

        scaled_w = w_zone * sc
        self._draw_mod_edit_box(
            p, "ribbon",
            QRectF(cx - scaled_w * 0.48, top_y - 6, scaled_w * 0.96, max(40, bottom_y - top_y + 12))
        )

        rel_x = [0.0] * steps
        slope = 0.0
        cur_x = 0.0
        max_offset = scaled_w * 0.42
        for s in range(car_step + 1, steps):
            pt = ref[(idx0 + (s - car_step) * step_stride) % n]
            t_ahead = (s - car_step) / float(steps - car_step - 1)
            steer_right_deg = -math.degrees(pt["SteeringWheelAngle"])
            slope += (steer_right_deg / 90.0) * 3.8 * sc
            cur_x += slope * (0.35 + 0.65 * t_ahead)
            rel_x[s] = max(-max_offset, min(max_offset, cur_x))

        slope_back = 0.0
        cur_x_back = 0.0
        for s in range(car_step - 1, -1, -1):
            pt = ref[(idx0 + (s - car_step) * step_stride) % n]
            steer_right_deg = -math.degrees(pt["SteeringWheelAngle"])
            slope_back -= (steer_right_deg / 90.0) * 2.0 * sc
            cur_x_back += slope_back * 0.35
            rel_x[s] = max(-max_offset, min(max_offset, cur_x_back))

        pts_left, pts_right, pts_center = [], [], []
        pts_in_l, pts_in_r = [], []
        seg_data = []

        for s in range(steps):
            pt = ref[(idx0 + (s - car_step) * step_stride) % n]
            t = s / float(steps - 1)
            persp_t = math.pow(t, 0.78)
            y = bottom_y - persp_t * (bottom_y - top_y)

            half_w = (1.0 - t * 0.80) * (scaled_w * 0.34)
            x_mid = cx + rel_x[s]
            pts_left.append(QPointF(x_mid - half_w, y))
            pts_right.append(QPointF(x_mid + half_w, y))
            pts_center.append(QPointF(x_mid, y))
            pts_in_l.append(QPointF(x_mid - half_w * 0.86, y))
            pts_in_r.append(QPointF(x_mid + half_w * 0.86, y))

            seg_data.append((pt["Brake"], pt["Throttle"], t, half_w, pt["LapDistPct"]))

        for s in range(steps - 1):
            brk, thr, t, half_w, seg_pct = seg_data[s]
            next_brk, next_thr, _, next_half_w, _ = seg_data[s + 1]

            if s < car_step:
                fade = math.pow((s + 0.5) / float(car_step), 1.45)
            else:
                t_top = (s - car_step) / float(max(1, steps - 1 - car_step))
                fade_start = 1.0 - top_fade_strength * 0.70
                if t_top <= fade_start:
                    fade = 1.0 - (t_top / max(0.05, fade_start)) * (0.15 * top_fade_strength)
                else:
                    rem = (t_top - fade_start) / max(0.05, 1.0 - fade_start)
                    start_val = 1.0 - 0.15 * top_fade_strength
                    fade = start_val * math.pow(max(0.0, 1.0 - rem), 1.0 + top_fade_strength * 2.2)

            alpha = int(240 * max(0.0, min(1.0, fade)))
            if alpha < 4:
                continue

            poly_road = QPolygonF([pts_left[s], pts_right[s], pts_right[s + 1], pts_left[s + 1]])
            p.setBrush(QBrush(QColor(12, 32, 45, int(alpha * 0.65))))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(poly_road)

            if brk > 0.05 or next_brk > 0.05:
                ref_col = QColor(255, 35, 65, int(alpha * 0.92)) if max(brk, next_brk) > 0.25 else QColor(255, 160, 30, int(alpha * 0.88))
                rail_col = QColor(255, 90, 115, min(255, int(255 * fade)))
                r_int0 = max(0.12, min(0.86, brk * 0.86))
                r_int1 = max(0.12, min(0.86, next_brk * 0.86))
            elif thr > 0.08 or next_thr > 0.08:
                ref_col = QColor(25, 240, 130, int(alpha * 0.78))
                rail_col = QColor(80, 245, 255, min(255, int(250 * fade)))
                r_int0 = max(0.12, min(0.86, thr * 0.86))
                r_int1 = max(0.12, min(0.86, next_thr * 0.86))
            else:
                ref_col = QColor(0, 195, 250, int(alpha * 0.50))
                rail_col = QColor(80, 225, 255, min(255, int(220 * fade)))
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
            p.setPen(QPen(QColor(255, 255, 255, int(70 * fade)), 1.0))
            p.drawPolygon(poly_ref)

            if self.state.cp_show_player_on_rib and s <= car_step:
                if s == car_step:
                    u_thr, u_brk = self.state.throttle, self.state.brake
                else:
                    u_thr, u_brk = self._find_player_input_at(seg_pct)

                u_val = u_brk if u_brk > 0.04 else (u_thr if u_thr > 0.04 else 0.0)
                if u_val > 0.04:
                    uw0 = half_w * min(0.86, u_val * 0.86)
                    uw1 = next_half_w * min(0.86, u_val * 0.86)
                    u_col = (
                        QColor(255, 215, 65, int(190 * fade))
                        if u_brk > 0.04
                        else QColor(120, 255, 200, int(175 * fade))
                    )
                    u_poly = QPolygonF([
                        QPointF(c0.x() - uw0, c0.y()),
                        QPointF(c0.x() + uw0, c0.y()),
                        QPointF(c1.x() + uw1, c1.y()),
                        QPointF(c1.x() - uw1, c1.y()),
                    ])
                    p.setBrush(QBrush(u_col))
                    p.setPen(QPen(QColor(255, 255, 255, int(210 * fade)), 1.2))
                    p.drawPolygon(u_poly)

            if s % 2 == 1 and s < steps - 2:
                chev_poly = QPolygonF([
                    pts_in_l[s],
                    QPointF(c0.x(), c0.y() - (c0.y() - c1.y()) * 0.45),
                    pts_in_r[s],
                    pts_in_r[s + 1],
                    QPointF(c1.x(), c1.y() - (c0.y() - c1.y()) * 0.45),
                    pts_in_l[s + 1],
                ])
                p.setBrush(QBrush(QColor(170, 240, 255, int(45 * fade))))
                p.setPen(QPen(QColor(195, 245, 255, int(80 * fade)), 1.0))
                p.drawPolygon(chev_poly)

            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(rail_col.red(), rail_col.green(), rail_col.blue(), int(90 * fade)), 5.0))
            p.drawLine(pts_left[s], pts_left[s + 1])
            p.drawLine(pts_right[s], pts_right[s + 1])

            p.setPen(QPen(rail_col, 2.2))
            p.drawLine(pts_left[s], pts_left[s + 1])
            p.drawLine(pts_right[s], pts_right[s + 1])

            p.setPen(QPen(QColor(210, 245, 255, int(105 * fade)), 1.0))
            p.drawLine(pts_in_l[s], pts_in_l[s + 1])
            p.drawLine(pts_in_r[s], pts_in_r[s + 1])
            p.drawLine(pts_center[s], pts_center[s + 1])

        if self.state.cp_show_player_on_rib:
            self._set_elem_opacity(p, self.state.cp_op_player_on_rib)
            pL = pts_left[car_step]
            pR = pts_right[car_step]
            pC = pts_center[car_step]
            hw = seg_data[car_step][3] * 0.86

            ref_b, ref_t = self.state.t_brake, self.state.t_throttle
            you_b, you_t = self.state.brake, self.state.throttle

            if ref_b > 0.08 or you_b > 0.08:
                ref_active = ref_b
                you_active = you_b
                base_bar_col = QColor(255, 50, 75)
            else:
                ref_active = ref_t
                you_active = you_t
                base_bar_col = QColor(40, 245, 140)

            diff_pedal = abs(you_active - ref_active)
            is_matched = (
                self.state.cp_show_pedal_match
                and (ref_active > 0.08 or you_active > 0.08)
                and diff_pedal <= 0.10
            )

            line_L = QPointF(pL.x() - 6, pL.y())
            line_R = QPointF(pR.x() + 6, pR.y())

            glow_col = QColor(0, 255, 195, 155) if is_matched else QColor(base_bar_col.red(), base_bar_col.green(), base_bar_col.blue(), 95)
            main_line_col = QColor(140, 255, 225, 255) if is_matched else QColor(230, 245, 255, 210)

            p.setPen(QPen(glow_col, 7))
            p.drawLine(line_L, line_R)
            p.setPen(QPen(main_line_col, 2.0))
            p.drawLine(line_L, line_R)

            if you_active > 0.02:
                uw = hw * min(1.0, you_active)
                fill_c = QColor(0, 255, 190) if is_matched else QColor(255, 225, 70) if (you_b > 0.05 or ref_b > 0.08) else QColor(65, 255, 145)
                p.setPen(QPen(fill_c, 5.0))
                p.drawLine(QPointF(pC.x() - uw, pC.y()), QPointF(pC.x() + uw, pC.y()))

                p.setPen(QPen(Qt.GlobalColor.white, 2.8))
                p.drawLine(QPointF(pC.x() - uw, pC.y() - 6), QPointF(pC.x() - uw, pC.y() + 6))
                p.drawLine(QPointF(pC.x() + uw, pC.y() - 6), QPointF(pC.x() + uw, pC.y() + 6))

            chev = QPolygonF([
                QPointF(pC.x(), pC.y() - 6),
                QPointF(pC.x() - 6, pC.y() + 4),
                QPointF(pC.x() + 6, pC.y() + 4),
            ])
            p.setBrush(QBrush(QColor(0, 255, 195) if is_matched else Qt.GlobalColor.white))
            p.setPen(QPen(QColor(10, 20, 30), 1.4))
            p.drawPolygon(chev)
            p.setBrush(Qt.BrushStyle.NoBrush)