from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QBrush, QLinearGradient, QPainterPath

from config import FONT_UI, FONT_MONO
from .base import DraggableHUDWidget


def draw_unified_pedal_bar(
    p: QPainter,
    x_center: int,
    top_y: int,
    bar_w: int,
    bar_h: int,
    you_val: float,
    ref_val: float,
    top_label: str,
    bottom_label: str,
    main_col: QColor,
    readout_side: str = "right",
    show_you_pct: bool = True,
    show_ref_pct: bool = True,
) -> QRectF:
    """
    1. ЗАДНИЙ ПЛАН (на всю ширину внутренней шахты, полупрозрачный) — ТВОЁ нажатие (YOU) + белая планка-ползунок.
    2. ПЕРЕДНИЙ ПЛАН (чуть уже, яркий неоновый градиент с чёткой кромкой) — ЭТАЛОН (REF), всегда виден поверх твоего фона.
    (Статичная серая надпись 0% снизу убрана).
    """
    x0 = x_center - bar_w // 2
    you_v = max(0.0, min(1.0, you_val))
    ref_v = max(0.0, min(1.0, ref_val))

    # 1. Заголовок сверху (BRAKE / THROTTLE)
    p.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
    p.setPen(main_col)
    p.drawText(x_center - 50, top_y - 24, 100, 18, Qt.AlignmentFlag.AlignCenter, top_label)

    # 2. Внешние технические засечки-скобки слева и справа от капсулы
    bracket_pen = QPen(QColor(130, 155, 175, 90), 1.0)
    p.setPen(bracket_pen)
    bx_l = x0 - 6
    bx_r = x0 + bar_w + 6
    p.drawLine(bx_l, top_y, bx_l + 3, top_y)
    p.drawLine(bx_l, top_y, bx_l, top_y + bar_h)
    p.drawLine(bx_l, top_y + bar_h, bx_l + 3, top_y + bar_h)

    p.drawLine(bx_r - 3, top_y, bx_r, top_y)
    p.drawLine(bx_r, top_y, bx_r, top_y + bar_h)
    p.drawLine(bx_r - 3, top_y + bar_h, bx_r, top_y + bar_h)

    # 3. Внешняя стеклянная капсула педали
    outer_rect = QRectF(x0, top_y, bar_w, bar_h)
    outer_path = QPainterPath()
    outer_path.addRoundedRect(outer_rect, 6, 6)
    p.fillPath(outer_path, QColor(8, 12, 18, 205))

    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(main_col.red(), main_col.green(), main_col.blue(), 55), 3.5))
    p.drawPath(outer_path)
    p.setPen(QPen(QColor(main_col.red(), main_col.green(), main_col.blue(), 175), 1.3))
    p.drawPath(outer_path)

    pad = max(3, int(bar_w * 0.11))
    ix = x0 + pad
    iw = bar_w - pad * 2
    iy = top_y + pad
    ih = bar_h - pad * 2

    inner_bg = QPainterPath()
    inner_bg.addRoundedRect(QRectF(ix, iy, iw, ih), 4, 4)
    p.fillPath(inner_bg, QColor(18, 24, 32, 170))

    # 4. ЗАДНИЙ ФОН — ТВОЁ НАЖАТИЕ (YOU)
    yh = int(ih * you_v)
    uy = iy + ih - yh
    if yh > 1:
        you_rect = QRectF(ix, uy, iw, yh)
        you_path = QPainterPath()
        you_path.addRoundedRect(you_rect, 3, 3)
        you_grad = QLinearGradient(0, uy, 0, iy + ih)
        you_grad.setColorAt(0.0, QColor(main_col.red(), main_col.green(), main_col.blue(), 110))
        you_grad.setColorAt(1.0, QColor(int(main_col.red() * 0.4), int(main_col.green() * 0.4), int(main_col.blue() * 0.4), 65))
        p.fillPath(you_path, QBrush(you_grad))

    # 5. ПЕРЕДНИЙ ПЛАН — ЭТАЛОН (REF)
    rh = int(ih * ref_v)
    ry = iy + ih - rh
    ref_pad = max(3, int(iw * 0.18))
    if rh > 1:
        ref_rect = QRectF(ix + ref_pad, ry, iw - ref_pad * 2, rh)
        ref_path = QPainterPath()
        ref_path.addRoundedRect(ref_rect, 3, 3)
        ref_grad = QLinearGradient(0, ry, 0, iy + ih)
        top_bright = QColor(
            min(255, main_col.red() + 75),
            min(255, main_col.green() + 75),
            min(255, main_col.blue() + 75),
            255,
        )
        ref_grad.setColorAt(0.0, top_bright)
        ref_grad.setColorAt(0.25, QColor(main_col.red(), main_col.green(), main_col.blue(), 245))
        ref_grad.setColorAt(1.0, QColor(int(main_col.red() * 0.50), int(main_col.green() * 0.50), int(main_col.blue() * 0.50), 225))
        p.fillPath(ref_path, QBrush(ref_grad))

        p.setPen(QPen(top_bright, 2.2))
        p.drawLine(QPointF(ix + ref_pad, ry), QPointF(ix + iw - ref_pad, ry))

    # 6. Яркая белая горизонтальная ПЛАНКА-ПОЛЗУНОК ТВОЕГО НАЖАТИЯ
    if you_v > 0.01:
        p.setPen(QPen(QColor(main_col.red(), main_col.green(), main_col.blue(), 160), 5.0))
        p.drawLine(QPointF(x0 - 1, uy), QPointF(x0 + bar_w + 1, uy))
        p.setPen(QPen(Qt.GlobalColor.white, 2.4))
        p.drawLine(QPointF(x0 - 2, uy), QPointF(x0 + bar_w + 2, uy))

    # 7. Опциональные подписи: бейдж "REF: XX%" и плавающий процент игрока "XX%"
    is_right = (readout_side == "right")
    ref_badge_y = max(top_y, min(top_y + bar_h - 18, int(ry - 9))) if ref_v > 0.02 else top_y + 2

    if show_ref_pct:
        badge_w, badge_h = 56, 17
        badge_x = (x0 + bar_w + 10) if is_right else (x0 - badge_w - 10)

        b_path = QPainterPath()
        b_path.addRoundedRect(QRectF(badge_x, ref_badge_y, badge_w, badge_h), 3, 3)
        p.fillPath(b_path, QColor(24, 32, 42, 215))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(main_col.red(), main_col.green(), main_col.blue(), 130), 1.0))
        p.drawPath(b_path)

        p.setFont(QFont(FONT_MONO, 7, QFont.Weight.Bold))
        p.setPen(QColor(220, 232, 245))
        p.drawText(int(badge_x), int(ref_badge_y), badge_w, badge_h, Qt.AlignmentFlag.AlignCenter, f"REF: {ref_v * 100:.0f}%")

    txt_x = (x0 + bar_w + 10) if is_right else (x0 - 58)
    align = Qt.AlignmentFlag.AlignLeft if is_right else Qt.AlignmentFlag.AlignRight

    if show_you_pct:
        you_lbl_y = max(top_y + 12, min(top_y + bar_h - 20, int(uy - 11)))
        if show_ref_pct and abs(you_lbl_y - ref_badge_y) < 18:
            you_lbl_y = min(top_y + bar_h - 18, ref_badge_y + 19)

        tick_x1 = (x0 + bar_w + 2) if is_right else (x0 - 2)
        tick_x2 = (x0 + bar_w + 7) if is_right else (x0 - 7)
        p.setPen(QPen(Qt.GlobalColor.white, 1.8))
        p.drawLine(tick_x1, int(uy), tick_x2, int(uy))

        p.setFont(QFont(FONT_MONO, 12, QFont.Weight.Bold))
        p.setPen(main_col)
        p.drawText(int(txt_x), int(you_lbl_y), 48, 22, align | Qt.AlignmentFlag.AlignVCenter, f"{you_v * 100:.0f}%")

    # Короткая подпись снизу (BRK / THR / CLT)
    p.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
    p.setPen(QColor(165, 180, 195))
    p.drawText(x_center - 40, top_y + bar_h + 6, 80, 18, Qt.AlignmentFlag.AlignCenter, bottom_label)

    total_left = x0 - 65 if not is_right else x0 - 10
    total_w = bar_w + 80
    return QRectF(total_left, top_y - 26, total_w, bar_h + 48)


class PedalsWidget(DraggableHUDWidget):
    def paintEvent(self, event):
        p = self.begin_hud_painter()
        self.draw_card_bg(p, 210)
        w, h = self.width(), self.height()

        top_y = 38
        bot_y = h - 28
        bar_h = max(30, bot_y - top_y)

        sy = self.state.pedals_show_you_pct
        sr = self.state.pedals_show_ref_pct

        if self.state.show_clutch:
            bw = max(20, min(32, int(w * 0.11)))
            draw_unified_pedal_bar(
                p, x_center=int(w * 0.16), top_y=top_y, bar_w=bw, bar_h=bar_h,
                you_val=self.state.clutch, ref_val=self.state.t_clutch,
                top_label="CLUTCH", bottom_label="CLT", main_col=QColor(0, 210, 255),
                readout_side="right", show_you_pct=sy, show_ref_pct=sr
            )
            draw_unified_pedal_bar(
                p, x_center=int(w * 0.48), top_y=top_y, bar_w=bw, bar_h=bar_h,
                you_val=self.state.brake, ref_val=self.state.t_brake,
                top_label="BRAKE", bottom_label="BRK", main_col=QColor(255, 45, 70),
                readout_side="right", show_you_pct=sy, show_ref_pct=sr
            )
            draw_unified_pedal_bar(
                p, x_center=int(w * 0.84), top_y=top_y, bar_w=bw, bar_h=bar_h,
                you_val=self.state.throttle, ref_val=self.state.t_throttle,
                top_label="THROTTLE", bottom_label="THR", main_col=QColor(35, 245, 130),
                readout_side="left", show_you_pct=sy, show_ref_pct=sr
            )
        else:
            bw = max(24, min(38, int(w * 0.14)))
            draw_unified_pedal_bar(
                p, x_center=int(w * 0.24), top_y=top_y, bar_w=bw, bar_h=bar_h,
                you_val=self.state.brake, ref_val=self.state.t_brake,
                top_label="BRAKE", bottom_label="BRK", main_col=QColor(255, 45, 70),
                readout_side="right", show_you_pct=sy, show_ref_pct=sr
            )
            draw_unified_pedal_bar(
                p, x_center=int(w * 0.76), top_y=top_y, bar_w=bw, bar_h=bar_h,
                you_val=self.state.throttle, ref_val=self.state.t_throttle,
                top_label="THROTTLE", bottom_label="THR", main_col=QColor(35, 245, 130),
                readout_side="left", show_you_pct=sy, show_ref_pct=sr
            )