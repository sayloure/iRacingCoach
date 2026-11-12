from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QBrush, QPainterPath, QLinearGradient

from config import FONT_UI, save_config


class DraggableHUDWidget(QWidget):
    """Прозрачный голографический виджет с поддержкой перемещения, изменения размера мышкой и F8."""
    RESIZE_MARGIN = 18

    def __init__(self, key_name, title, default_rect, state, layout_cfg, default_bg_pct=75, min_size=(170, 90)):
        super().__init__()
        self.key_name = key_name
        self.hud_title = title
        self.state = state
        self.layout_cfg = layout_cfg
        self.locked = False
        self._drag_pos = None
        self._resizing = False
        self._resize_start_mouse = None
        self._resize_start_geom = None
        self.setMinimumSize(min_size[0], min_size[1])
        self.setMouseTracking(True)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        saved = self.layout_cfg.get(self.key_name, {})
        x = saved.get("x", default_rect[0])
        y = saved.get("y", default_rect[1])
        w = max(min_size[0], saved.get("w", default_rect[2]))
        h = max(min_size[1], saved.get("h", default_rect[3]))
        self.bg_opacity_pct = saved.get("bg_opacity", default_bg_pct)
        self.fg_opacity_pct = saved.get("fg_opacity", 100)
        self.setGeometry(x, y, w, h)

    def _in_resize_zone(self, local_pos) -> bool:
        return (
            not self.locked
            and local_pos.x() >= self.width() - self.RESIZE_MARGIN
            and local_pos.y() >= self.height() - self.RESIZE_MARGIN
        )

    def set_bg_opacity(self, pct: int):
        self.bg_opacity_pct = max(0, min(100, int(pct)))
        self._save_state()
        self.update()

    def set_fg_opacity(self, pct: int):
        self.fg_opacity_pct = max(10, min(100, int(pct)))
        self._save_state()
        self.update()

    def set_locked(self, locked: bool):
        self.locked = locked
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, locked)
        self.show()
        self.update()

    def _save_state(self):
        self.layout_cfg[self.key_name] = {
            "x": self.x(), "y": self.y(), "w": self.width(), "h": self.height(),
            "enabled": self.layout_cfg.get(self.key_name, {}).get("enabled", True),
            "bg_opacity": self.bg_opacity_pct,
            "fg_opacity": self.fg_opacity_pct,
        }
        save_config(self.layout_cfg)

    def mousePressEvent(self, event):
        if self.locked or event.button() != Qt.MouseButton.LeftButton:
            return
        local_pt = event.position().toPoint()
        if self._in_resize_zone(local_pt):
            self._resizing = True
            self._resize_start_mouse = event.globalPosition().toPoint()
            self._resize_start_geom = self.geometry()
        else:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self.locked:
            return
        local_pt = event.position().toPoint()
        if self._resizing and self._resize_start_mouse is not None:
            delta = event.globalPosition().toPoint() - self._resize_start_mouse
            new_w = max(self.minimumWidth(), self._resize_start_geom.width() + delta.x())
            new_h = max(self.minimumHeight(), self._resize_start_geom.height() + delta.y())
            self.resize(new_w, new_h)
            self.update()
            return
        if self._drag_pos is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            return

        if self._in_resize_zone(local_pt):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event):
        if self._drag_pos is not None or self._resizing:
            self._drag_pos = None
            self._resizing = False
            self._save_state()

    def begin_hud_painter(self) -> QPainter:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self.fg_opacity_pct / 100.0)
        return p

    def draw_card_bg(self, p: QPainter, max_alpha=215):
        w, h = self.width(), self.height()
        path = QPainterPath()
        path.addRoundedRect(QRectF(1.5, 1.5, w - 3, h - 3), 12, 12)
        bg_scale = self.bg_opacity_pct / 100.0
        alpha = int(max_alpha * bg_scale)

        if alpha > 0:
            grad = QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0.0, QColor(16, 28, 36, alpha))
            grad.setColorAt(0.5, QColor(10, 16, 22, int(alpha * 0.92)))
            grad.setColorAt(1.0, QColor(6, 10, 15, alpha))
            p.fillPath(path, QBrush(grad))

            top_glow = QLinearGradient(0, 0, w, 0)
            top_glow.setColorAt(0.0, QColor(0, 240, 255, 0))
            top_glow.setColorAt(0.25, QColor(80, 245, 255, int(130 * bg_scale)))
            top_glow.setColorAt(0.75, QColor(80, 245, 255, int(90 * bg_scale)))
            top_glow.setColorAt(1.0, QColor(0, 240, 255, 0))
            p.setPen(QPen(QBrush(top_glow), 1.8))
            p.drawLine(14, 2, w - 14, 2)

        if not self.locked:
            p.setPen(QPen(QColor(0, 225, 255, 200), 1.5, Qt.PenStyle.DashLine))
            p.drawPath(path)
            p.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
            p.setPen(QColor(0, 230, 255, 230))
            p.drawText(10, 15, f"✥ {self.hud_title}")

            p.setPen(QPen(QColor(0, 235, 255, 230), 2))
            for offset in (4, 8, 12):
                p.drawLine(w - offset, h - 3, w - 3, h - offset)
        elif alpha > 12:
            p.setPen(QPen(QColor(110, 210, 230, int(55 * bg_scale)), 1.1))
            p.drawPath(path)