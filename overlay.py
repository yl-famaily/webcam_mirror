"""바탕화면 위에 뜨는 프레임리스 웹캠 창."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from settings import (
    MIN_H,
    MIN_W,
    SHAPE_ELLIPSE,
    SHAPE_NONE,
    Settings,
    fit_to_ratio,
    ratio_of,
)

EDGE = 10  # 가장자리 리사이즈 감지 폭(px)

_NONE = 0
_LEFT = 1
_RIGHT = 2
_TOP = 4
_BOTTOM = 8

_CURSORS = {
    _LEFT: Qt.CursorShape.SizeHorCursor,
    _RIGHT: Qt.CursorShape.SizeHorCursor,
    _TOP: Qt.CursorShape.SizeVerCursor,
    _BOTTOM: Qt.CursorShape.SizeVerCursor,
    _LEFT | _TOP: Qt.CursorShape.SizeFDiagCursor,
    _RIGHT | _BOTTOM: Qt.CursorShape.SizeFDiagCursor,
    _RIGHT | _TOP: Qt.CursorShape.SizeBDiagCursor,
    _LEFT | _BOTTOM: Qt.CursorShape.SizeBDiagCursor,
}


class OverlayWindow(QWidget):
    settings_changed = Signal()

    def __init__(self, settings: Settings):
        super().__init__()
        self.s = settings
        self.controller = None  # main.AppController 가 주입

        self._frame: QImage | None = None
        self._status = "카메라 여는 중..."
        self._drag_offset: QPoint | None = None
        self._resize_edge = _NONE
        self._resize_start_geo: QRect | None = None
        self._resize_start_pos: QPoint | None = None
        self._syncing = False

        self.setWindowTitle("Webcam Mirror")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # 작업표시줄/Alt+Tab 에서 제외
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setMinimumSize(MIN_W, MIN_H)
        self.setCursor(Qt.CursorShape.SizeAllCursor)

        self.setGeometry(self.s.x, self.s.y, self.s.w, self.s.h)
        self.setWindowOpacity(self.s.opacity / 100.0)

    # ------------------------------------------------------------ 프레임 --

    def on_frame(self, image: QImage) -> None:
        self._frame = image
        self._status = ""
        self.update()

    def on_error(self, message: str) -> None:
        self._frame = None
        self._status = message
        self.update()

    def set_status(self, message: str) -> None:
        self._frame = None
        self._status = message
        self.update()

    # ------------------------------------------------------------- 그리기 --

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        border = self.s.border_width
        half = border / 2.0
        inner = QRectF(self.rect()).adjusted(half, half, -half, -half)

        shaped = self.s.shape != SHAPE_NONE
        path = QPainterPath()
        if self.s.shape == SHAPE_ELLIPSE:
            path.addEllipse(inner)
        else:
            path.addRect(inner)

        if shaped:
            painter.setClipPath(path)
        if self._frame is not None and not self._frame.isNull():
            source = self._cover_rect(self._frame, inner)
            if self.s.mirror:
                painter.save()
                # inner 의 세로 중심선을 기준으로 좌우 반전
                painter.translate(inner.left() * 2 + inner.width(), 0)
                painter.scale(-1, 1)
                painter.drawImage(inner, self._frame, source)
                painter.restore()
            else:
                painter.drawImage(inner, self._frame, source)
        else:
            painter.fillPath(path, QColor(24, 24, 28, 220))
            painter.setPen(QColor(235, 235, 235))
            font = painter.font()
            font.setPointSize(9)
            painter.setFont(font)
            painter.drawText(
                self.rect().adjusted(12, 12, -12, -12),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self._status,
            )
        painter.setClipping(False)

        # 모양 '없음' 은 배경 제거와 함께 쓰라고 있는 것이라 테두리를 그리지 않는다.
        if border > 0 and shaped:
            pen = QPen(QColor(self.s.border_color), border)
            pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

    @staticmethod
    def _cover_rect(image: QImage, target: QRectF) -> QRectF:
        """타깃 비율에 맞게 원본을 중앙 기준으로 잘라낸다(cover). 비율 왜곡 없음."""
        iw, ih = image.width(), image.height()
        if iw <= 0 or ih <= 0 or target.width() <= 0 or target.height() <= 0:
            return QRectF(0, 0, iw, ih)

        target_ar = target.width() / target.height()
        image_ar = iw / ih
        if image_ar > target_ar:  # 원본이 더 넓다 -> 좌우를 자른다
            sw, sh = ih * target_ar, float(ih)
        else:  # 원본이 더 높다 -> 위아래를 자른다
            sw, sh = float(iw), iw / target_ar
        return QRectF((iw - sw) / 2.0, (ih - sh) / 2.0, sw, sh)

    # -------------------------------------------------------------- 마우스 --

    def _edge_at(self, pos: QPoint) -> int:
        edge = _NONE
        if pos.x() <= EDGE:
            edge |= _LEFT
        elif pos.x() >= self.width() - EDGE:
            edge |= _RIGHT
        if pos.y() <= EDGE:
            edge |= _TOP
        elif pos.y() >= self.height() - EDGE:
            edge |= _BOTTOM
        return edge

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self.controller:
            # 좌드래그=이동, 좌더블=모양, 휠=줌, 우클릭=메뉴가 이미 차 있어
            # 남은 제스처는 가운데 버튼뿐이다.
            self.controller.toggle_panel()
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        global_pos = event.globalPosition().toPoint()
        edge = self._edge_at(event.position().toPoint())
        if edge:
            self._resize_edge = edge
            self._resize_start_geo = QRect(self.geometry())
            self._resize_start_pos = global_pos
        else:
            self._drag_offset = global_pos - self.frameGeometry().topLeft()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        global_pos = event.globalPosition().toPoint()
        if self._resize_edge:
            keep_ratio = self.s.lock_aspect or bool(
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            )
            self._do_resize(global_pos, keep_ratio)
        elif self._drag_offset is not None:
            self.move(global_pos - self._drag_offset)
        else:
            self.setCursor(
                _CURSORS.get(
                    self._edge_at(event.position().toPoint()),
                    Qt.CursorShape.SizeAllCursor,
                )
            )
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._resize_edge = _NONE
        self._resize_start_geo = None
        self._resize_start_pos = None
        self._drag_offset = None
        self._sync_geometry()
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        """더블클릭으로 원형 <-> 사각형 전환."""
        if event.button() == Qt.MouseButton.LeftButton and self.controller:
            self.controller.toggle_shape()
        event.accept()

    def _do_resize(self, global_pos: QPoint, keep_ratio: bool) -> None:
        start = self._resize_start_geo
        if start is None or self._resize_start_pos is None:
            return
        delta = global_pos - self._resize_start_pos

        left, top = start.left(), start.top()
        right, bottom = start.right(), start.bottom()
        if self._resize_edge & _LEFT:
            left += delta.x()
        if self._resize_edge & _RIGHT:
            right += delta.x()
        if self._resize_edge & _TOP:
            top += delta.y()
        if self._resize_edge & _BOTTOM:
            bottom += delta.y()

        width = max(MIN_W, right - left + 1)
        height = max(MIN_H, bottom - top + 1)

        ratio = ratio_of(self.s.aspect)
        if ratio is None and keep_ratio:
            ratio = start.width() / max(1, start.height())  # 자유 + Shift
        if ratio is not None:
            # 더 많이 끌린 축을 기준으로 나머지를 맞춘다
            prefer_width = abs(width - start.width()) >= abs(height - start.height())
            width, height = fit_to_ratio(width, height, ratio, prefer_width)

        # 잡지 않은 쪽 모서리는 고정
        new_x = right - width + 1 if (self._resize_edge & _LEFT) else left
        new_y = bottom - height + 1 if (self._resize_edge & _TOP) else top
        self.setGeometry(new_x, new_y, width, height)

    def wheelEvent(self, event) -> None:
        steps = event.angleDelta().y() / 120.0
        if not steps:
            return
        factor = 1.0 + 0.06 * steps
        geo = self.geometry()
        center = geo.center()
        width = max(MIN_W, round(geo.width() * factor))
        height = max(MIN_H, round(geo.height() * factor))
        ratio = ratio_of(self.s.aspect)
        if ratio is not None:
            width, height = fit_to_ratio(width, height, ratio, prefer_width=True)
        self.setGeometry(
            center.x() - width // 2, center.y() - height // 2, width, height
        )
        self._sync_geometry()
        event.accept()

    def contextMenuEvent(self, event) -> None:
        if self.controller is None:
            return
        # 메뉴를 매번 새로 만들면 오버레이의 자식으로 계속 쌓인다 (액션 70여 개 +
        # 서브메뉴 12개 x 우클릭 횟수). 컨트롤러가 하나만 들고 재사용한다.
        self.controller.context_menu().exec(event.globalPos())
        event.accept()

    # --------------------------------------------------------------- 상태 --

    def moveEvent(self, event) -> None:
        self._sync_geometry()
        if self.controller is not None:
            self.controller.reposition_panel()

    def resizeEvent(self, event) -> None:
        self._sync_geometry()
        if self.controller is not None:
            self.controller.reposition_panel()

    def _sync_geometry(self) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            geo = self.geometry()
            self.s.x, self.s.y = geo.x(), geo.y()
            self.s.w, self.s.h = geo.width(), geo.height()
            # 효과가 계산할 해상도는 **실제로 칠해지는 픽셀 수**로 정해야 한다.
            # 250% 배율 화면에서는 494 논리픽셀 창이 1235 물리픽셀로 그려진다.
            # 논리 크기만 보고 계산하면 그만큼 흐려진다. 두 값을 따로 저장하면
            # 캡처 스레드가 새 폭과 낡은 높이를 함께 읽어 한 프레임짜리 엉뚱한
            # 비율이 나올 수 있으므로 튜플 하나로 한 번에 발행한다.
            # dataclass 필드가 아니라서 Settings.save() 의 asdict 에는 안 들어간다.
            dpr = self.devicePixelRatioF() or 1.0
            self.s.view_size = (max(1, round(geo.width() * dpr)),
                                max(1, round(geo.height() * dpr)))
            self.s.placed = True
            self.settings_changed.emit()
        finally:
            self._syncing = False
