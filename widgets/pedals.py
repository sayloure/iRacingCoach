from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QBrush, QLinearGradient

from config import FONT_UI, FONT_MONO
from .base import DraggableHUDWidget


def draw_unified_pedal_bar(p: QPainter, x_center: int, top_y: int, bar_w: int, bar_h: int,
                           you_val: float, ref_val: float, label: str, main_col: QColor):
    """
    - Сзади на всю ширину стакана (полупрозрачный фон с кромкой) — ТВОЁ нажатие (YOU).
    - Спереди внутри стакана (яркий неоновый градиент + светящаяся белая планка) — ЭТАЛОН (REF).
    """
    x0 = x_center - bar_w // 2
    you_v = max(0.0, min(1.0, you_val))
    ref_v = max(0.0, min(1.0, ref_val))

    p.setFont(QFont(FONT_MONO, 8 if bar_w < 26 else 9, QFont.Weight.Bold))
    p.setPen(QColor(225, 235, 245))
    p.drawText(x_center - 46, top_y - 20, 92, 16, Qt.AlignmentFlag.AlignCenter,
               f"{you_v * 100:.0f}/{ref_v * 100:.0f}%")

    p.fillRect(x0, top_y, bar_w, bar_h, QColor(10, 15, 22, 195))

    # 1. ЗАДНИЙ ФОН — ТВОЁ НАЖАТИЕ (YOU)
    yh = int(bar_h * you_v)
    if yh > 0:
        you_bg = QColor(main_col.red(), main_col.green(), main_col.blue(), 75)
        p.fillRect(x0 + 1, top_y + bar_h - yh, bar_w - 2, yh, you_bg)
        uy = top_y + bar_h - yh
        p.setPen(QPen(QColor(255, 255, 255, 160), 1.5))
        p.drawLine(x0, uy, x0 + bar_w, uy)

    # 2. ПЕРЕДНИЙ ПЛАН — ЭТАЛОН (REF)
    rh = int(bar_h * ref_v)
    pad = max(3, bar_w // 6)
    if rh > 0:
        ry = top_y + bar_h - rh
        grad = QLinearGradient(0, ry, 0, top_y + bar_h)
        grad.setColorAt(0.0, QColor(min(255, main_col.red() + 55), min(255, main_col.green() + 55), min(255, main_col.blue() + 55), 255))
        grad.setColorAt(0.3, QColor(main_col.red(), main_col.green(), main_col.blue(), 245))
        grad.setColorAt(1.0, QColor(int(main_col.red() * 0.55), int(main_col.green() * 0.55), int(main_col.blue() * 0.55), 230))
        p.fillRect(QRectF(x0 + pad, ry, bar_w - pad * 2, rh), QBrush(grad))

        p.setPen(QPen(QColor(main_col.red(), main_col.green(), main_col.blue(), 140), 5))
        p.drawLine(x0 - 1, ry, x0 + bar_w + 1, ry)
        p.setPen(QPen(Qt.GlobalColor.white, 2.5))
        p.drawLine(x0 - 2, ry, x0 + bar_w + 2, ry)

    diff = abs(you_v - ref_v)
    border_col = QColor(60, 255, 180, 220) if (ref_v > 0.1 and diff < 0.08) else QColor(110, 140, 165, 150)
    p.setPen(QPen(border_col, 1.3))
    p.drawRect(x0, top_y, bar_w, bar_h)

    p.setFont(QFont(FONT_UI, 7 if bar_w < 26 else 8, QFont.Weight.Bold))
    p.setPen(QColor(200, 215, 230))
    p.drawText(x_center - 42, top_y + bar_h + 4, 84, 16, Qt.AlignmentFlag.AlignCenter, label)


class PedalsWidget(DraggableHUDWidget):
    def paintEvent(self, event):
        p = self.begin_hud_painter()
        self.draw_card_bg(p, 210)
        w, h = self.width(), self.height()

        p.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
        p.setPen(QColor(170, 190, 210))
        p.drawText(10, 6, w - 20, 18, Qt.AlignmentFlag.AlignCenter, "PEDALS: YOU vs REF")

        top_y = 46
        bot_y = h - 24
        bar_h = max(24, bot_y - top_y)

        if self.state.show_clutch:
            bw = max(20, min(38, int(w * 0.16)))
            draw_unified_pedal_bar(
                p, x_center=w // 6 + 4, top_y=top_y, bar_w=bw, bar_h=bar_h,
                you_val=self.state.clutch, ref_val=self.state.t_clutch,
                label="CLUTCH", main_col=QColor(0, 210, 255)
            )
            draw_unified_pedal_bar(
                p, x_center=w // 2, top_y=top_y, bar_w=bw, bar_h=bar_h,
                you_val=self.state.brake, ref_val=self.state.t_brake,
                label="BRAKE", main_col=QColor(255, 45, 60)
            )
            draw_unified_pedal_bar(
                p, x_center=(w * 5) // 6 - 4, top_y=top_y, bar_w=bw, bar_h=bar_h,
                you_val=self.state.throttle, ref_val=self.state.t_throttle,
                label="THROTTLE", main_col=QColor(40, 250, 110)
            )
        else:
            bw = max(24, min(46, int(w * 0.22)))
            draw_unified_pedal_bar(
                p, x_center=w // 4 + 4, top_y=top_y, bar_w=bw, bar_h=bar_h,
                you_val=self.state.brake, ref_val=self.state.t_brake,
                label="BRAKE", main_col=QColor(255, 45, 60)
            )
            draw_unified_pedal_bar(
                p, x_center=(w * 3) // 4 - 4, top_y=top_y, bar_w=bw, bar_h=bar_h,
                you_val=self.state.throttle, ref_val=self.state.t_throttle,
                label="THROTTLE", main_col=QColor(40, 250, 110)
            )