"""설정 패널 — 슬라이더로 조절하는 것들을 모아 둔 플로팅 창.

메뉴가 아니라 별도 창인 이유는 두 가지다. 메뉴는 항목을 누르면 무조건 닫혀서
값을 보며 맞출 수 없고, 팝업이 오버레이를 덮어 결과가 안 보인다. 패널은
오버레이 **옆에** 떠서 드래그하는 내내 화면을 보면서 조절할 수 있다.

방송 중에 쓰는 앱이라 `WA_ShowWithoutActivating` 으로 뜰 때 포커스를 뺏지
않는다. 슬라이더를 클릭하는 순간에만 활성화되는데, 이건 Qt 에서 피할 수 없고
사용자가 의도해서 누른 시점이라 허용한다.
"""

from __future__ import annotations

import os
import time

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, Signal, QTimer
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QButtonGroup,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QStyle,
    QVBoxLayout,
    QWidget,
)

import backgrounds
import theme
from effects import model_available
from settings import (
    BG_BLUR,
    BG_COLOR,
    BG_IMAGE,
    BG_OFF,
    BG_TRANSPARENT,
    SHAPE_NONE,
)

PANEL_W = 308  # #card 폭
GUTTER = theme.SHADOW_GUTTER
GAP = 8  # 카드와 오버레이 사이 시각적 간격
EDGE = 12  # 화면 가장자리 최소 여백
CARD_BORDER = 1  # QFrame#Card 의 테두리 두께 (theme.QSS 와 맞춰야 한다)

SPEED_WORDS = ("아주 느리게", "느리게", "보통", "빠르게", "아주 빠르게")
SIZE_WORDS = ("아주 작게", "작게", "보통", "크게", "아주 크게")


def _word(value: int, words: tuple[str, ...]) -> str:
    """1~10 을 5단계 꼬리표로. 숫자가 주, 꼬리표가 부다."""
    return words[min(len(words) - 1, (max(1, value) - 1) // 2)]


# ------------------------------------------------------------- 기본 위젯 --


class ToggleSwitch(QAbstractButton):
    """QCheckBox 는 QSS `image:` 없이는 스위치 모양이 안 나온다. 직접 그린다.

    QPushButton 이 아니라 QAbstractButton 을 상속하는 게 중요하다. 전역
    `QPushButton {{ min-height: 26px; border: 1px }}` 규칙은 서브클래스에도
    적용되는데, 그 min-height 가 `setFixedSize` 를 이겨서 44x24 로 지정해도
    44x28 로 그려졌다. paintEvent 는 24 기준으로 칠하니 아래 4px 가 비고
    스위치가 같은 줄 라벨보다 2px 위로 떴다. QAbstractButton 은 그 선택자에
    안 걸리면서 checkable·Space 키·clicked 는 그대로 준다.
    """

    TRACK_W, TRACK_H = 44, 24
    W = TRACK_W + theme.FOCUS_PAD * 2
    H = TRACK_H + theme.FOCUS_PAD * 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def sizeHint(self):
        return QSize(self.W, self.H)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 하드코딩 대신 rect() 에서 뺀다 — QSS 를 만져 상자가 커져도 안 어긋난다.
        pad = theme.FOCUS_PAD
        box = self.rect().adjusted(pad, pad, -pad, -pad)
        on = self.isChecked() and self.isEnabled()
        track = theme.ACCENT if on else theme.TRACK
        if not self.isEnabled():
            track = theme.DIVIDER
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(track))
        radius = box.height() / 2
        p.drawRoundedRect(box, radius, radius)
        knob = box.height() - 8
        x = box.right() - knob - 3 if self.isChecked() else box.left() + 4
        p.setBrush(QColor(theme.KNOB if self.isEnabled() else theme.FG_DISABLED))
        p.drawEllipse(x, box.top() + 4, knob, knob)
        if self.hasFocus():
            theme.paint_focus_ring(p, self.rect(), self.height() // 2)


class Swatch(QAbstractButton):
    """색 견본. 인스턴스마다 색이 달라 QSS 로는 못 만든다.

    ToggleSwitch 와 같은 이유로 QAbstractButton 을 상속한다 — 전역 QPushButton
    규칙의 min-height 가 setFixedSize 를 이기는 문제.
    """

    SIZE = 24  # 눈에 보이는 견본 크기
    BOX = SIZE + theme.FOCUS_PAD * 2  # 포커스 링 자리를 더한 위젯 크기

    def __init__(self, color: str | None, parent=None):
        super().__init__(parent)
        self.color = color  # None 이면 "직접 선택" 버튼
        self.setCheckable(color is not None)
        self.setFixedSize(self.BOX, self.BOX)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip(color or "직접 선택")
        self.setAccessibleName(color or "직접 선택")

    def sizeHint(self):
        return QSize(self.BOX, self.BOX)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        focused = self.hasFocus()
        # 비활성일 땐 흐리게 — 라벨만 회색이고 견본은 쨍하면 아직 고를 수 있어
        # 보인다. 회색 처리 규율은 컨트롤 전체에 적용돼야 한다.
        if not self.isEnabled():
            p.setOpacity(0.30)
        pad = theme.FOCUS_PAD
        box = self.rect().adjusted(pad, pad, -pad, -pad)
        rect = box.adjusted(2, 2, -2, -2)
        p.setPen(Qt.PenStyle.NoPen)
        if self.color is None:
            # 사분면 무지개로 "직접 선택"을 나타낸다
            path = QPainterPath()
            path.addRoundedRect(rect, theme.R_SWATCH, theme.R_SWATCH)
            p.setClipPath(path)
            for i, c in enumerate(("#ff5f5f", "#ffd25f", "#5fd0ff", "#a05fff")):
                p.setBrush(QColor(c))
                p.drawRect(
                    rect.x() + (i % 2) * rect.width() // 2,
                    rect.y() + (i // 2) * rect.height() // 2,
                    rect.width() // 2 + 1,
                    rect.height() // 2 + 1,
                )
            p.setClipping(False)
        else:
            p.setBrush(QColor(self.color))
            p.drawRoundedRect(rect, theme.R_SWATCH, theme.R_SWATCH)
        if self.isChecked():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QColor(theme.ACCENT))
            p.drawRoundedRect(box.adjusted(0, 0, -1, -1), 8, 8)
        if focused:
            p.setOpacity(1.0)  # 비활성이어도 포커스는 또렷해야 위치를 안다
            theme.paint_focus_ring(p, self.rect(), theme.R_SWATCH + pad)


class JumpSlider(QSlider):
    """그루브 아무 데나 눌러도 그 지점으로 바로 가고 드래그가 이어진다.

    기본 QSlider 는 핸들을 정확히 집어야 드래그가 시작되고, 빈 곳을 누르면
    페이지 스텝만 한 번 되고 만다. 좁은 패널에서는 못 쓸 동작이다.
    """

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setFixedHeight(theme.SLIDER_H)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        # QSS `QSlider:focus { border }` 는 내용 사각형을 줄여 groove 를 좌우
        # 1px 씩 민다. 채워진 구간이 흔들리고 _value_at 의 span 까지 바뀐다.
        if self.hasFocus():
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            theme.paint_focus_ring(p, self.rect(), 6)

    def _value_at(self, x: int) -> int:
        opt = self._opt()
        rect = self.style().subControlRect
        groove = rect(
            QStyle.ComplexControl.CC_Slider, opt,
            QStyle.SubControl.SC_SliderGroove, self,
        )
        handle = rect(
            QStyle.ComplexControl.CC_Slider, opt,
            QStyle.SubControl.SC_SliderHandle, self,
        )
        span = max(1, groove.width() - handle.width())
        pos = x - groove.x() - handle.width() / 2
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), int(pos), span
        )

    def mousePressEvent(self, event) -> None:
        # super() 에 넘기지 않고 직접 처리한다. 값을 스냅한 뒤 super() 를 부르면,
        # 눈금 간격이 핸들보다 넓은 슬라이더(1~10 은 간격 29px, 핸들 18px)에서
        # 클릭 지점이 핸들 밖에 남아 드래그가 시작되지 않는다 — 실측 35% 실패.
        if event.button() == Qt.MouseButton.LeftButton:
            self.setValue(self._value_at(event.position().toPoint().x()))
            self.setSliderDown(True)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.isSliderDown():
            self.setValue(self._value_at(event.position().toPoint().x()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            self.setSliderDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _opt(self):
        from PySide6.QtWidgets import QStyleOptionSlider

        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        return opt


class SliderRow(QWidget):
    """라벨 + 값 + 슬라이더 한 줄. 값 폭을 고정해 자릿수가 변해도 안 흔들린다."""

    changed = Signal(int)

    def __init__(self, label: str, lo: int, hi: int, fmt, step: int = 1, parent=None):
        super().__init__(parent)
        self._fmt = fmt
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(label)
        self.value = QLabel()
        self.value.setProperty("role", "value")
        self.value.setFixedWidth(64)
        self.value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        head.addWidget(self.label)
        head.addStretch(1)
        head.addWidget(self.value)
        box.addLayout(head)

        self.slider = JumpSlider()
        self.slider.setRange(lo, hi)
        self.slider.setSingleStep(step)
        self.slider.setPageStep(max(step, (hi - lo) // 8))
        self.slider.setTracking(True)  # 드래그 중 실시간 반영 — 이 패널의 존재 이유
        self.slider.setAccessibleName(label)
        self.slider.valueChanged.connect(self._on_change)
        box.addWidget(self.slider)

    def _on_change(self, value: int) -> None:
        self.value.setText(self._fmt(value))
        self.changed.emit(value)

    def set_value(self, value: int) -> None:
        blocked = self.slider.blockSignals(True)
        self.slider.setValue(value)
        self.slider.blockSignals(blocked)
        self.value.setText(self._fmt(value))

    def set_enabled(self, on: bool, why: str = "") -> None:
        for w in (self.label, self.value, self.slider):
            w.setEnabled(on)
        self.setToolTip("" if on else why)


class Segmented(QWidget):
    """배타 선택 버튼 줄. QPushButton + QButtonGroup 이면 충분하다."""

    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], parent=None, name: str = ""):
        super().__init__(parent)
        self.setAccessibleName(name)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self._by_key: dict[str, QPushButton] = {}
        for label, key in options:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            btn.setProperty("seg", "true")  # theme.QSS 가 좌우 여백을 줄인다
            btn.setAccessibleName("%s %s" % (name, label) if name else label)
            btn.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self.group.addButton(btn)
            row.addWidget(btn, 1)
            self._by_key[key] = btn

    def set_value(self, key: str) -> None:
        btn = self._by_key.get(key)
        # 비활성 버튼은 체크하지 않는다 (모델이 없을 때 '끄기' 외의 상태)
        if btn is not None and btn.isEnabled() and not btn.isChecked():
            btn.setChecked(True)

    def set_enabled_keys(self, keys: set[str]) -> None:
        for key, btn in self._by_key.items():
            btn.setEnabled(key in keys)


class SwatchRow(QWidget):
    changed = Signal(str)
    custom = Signal()

    def __init__(self, label: str, colors: list[str], parent=None):
        super().__init__(parent)
        self.setAccessibleName(label)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.label = QLabel(label)
        row.addWidget(self.label)
        row.addStretch(1)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self._swatches: list[Swatch] = []
        for color in colors:
            sw = Swatch(color)
            sw.setAccessibleName("%s %s" % (label, color))
            sw.clicked.connect(lambda _=False, c=color: self.changed.emit(c))
            self.group.addButton(sw)
            row.addWidget(sw)
            self._swatches.append(sw)
        more = Swatch(None)
        more.setAccessibleName("%s 직접 선택" % label)
        more.clicked.connect(lambda: self.custom.emit())
        row.addWidget(more)
        self._swatches.append(more)

    def set_value(self, color: str) -> None:
        # 배타 QButtonGroup 은 "현재 체크된 버튼의 해제"를 무시한다. 그래서
        # 직접 고른 색(#123456 등)으로 바꿔도 이전 견본에 선택 링이 남는다.
        # 잠깐 배타를 풀고 갱신한 뒤 되돌린다.
        target = (color or "").lower()
        self.group.setExclusive(False)
        for sw in self._swatches:
            if sw.color is not None:
                sw.setChecked(sw.color.lower() == target)
        self.group.setExclusive(True)

    def set_enabled(self, on: bool, why: str = "") -> None:
        self.label.setEnabled(on)
        for sw in self._swatches:
            sw.setEnabled(on)
        self.setToolTip("" if on else why)


class Thumb(QAbstractButton):
    """배경 이미지 견본. Swatch 와 같은 이유로 QAbstractButton 을 상속한다.

    16:9 로 그리는 게 중요하다 — 정사각 썸네일은 실제로 화면에 깔렸을 때 어떻게
    잘릴지 알려주지 못한다.
    """

    # 카드 안쪽 폭이 266 이라 (썸네일 5 + 파일 1) x 상자폭 <= 266 이어야 한다.
    # 상자 사이 간격은 0 으로 두고 각자의 포커스 여백(3px x 2)이 간격 역할을 한다.
    W, H = 38, 24
    BOX_W = W + theme.FOCUS_PAD * 2  # 44 -> 6개면 264
    BOX_H = H + theme.FOCUS_PAD * 2

    def __init__(self, key: str | None, label: str, parent=None):
        super().__init__(parent)
        self.key = key  # None 이면 "파일에서…" 버튼
        self._pixmap: QPixmap | None = None
        self.setCheckable(key is not None)
        self.setFixedSize(self.BOX_W, self.BOX_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip(label)
        self.setAccessibleName("배경 이미지 %s" % label)

    def sizeHint(self):
        return QSize(self.BOX_W, self.BOX_H)

    def set_preview(self, pixmap: QPixmap | None) -> None:
        self._pixmap = pixmap
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self.isEnabled():
            p.setOpacity(0.30)
        pad = theme.FOCUS_PAD
        box = self.rect().adjusted(pad, pad, -pad, -pad)
        inner = box.adjusted(2, 2, -2, -2)
        path = QPainterPath()
        path.addRoundedRect(inner, theme.R_SWATCH, theme.R_SWATCH)
        p.setClipPath(path)
        if self._pixmap is not None:
            p.drawPixmap(inner, self._pixmap)
        else:
            p.fillRect(inner, QColor(theme.TRACK))
        p.setClipping(False)
        if self.key is None:
            # "파일에서…" — 가운데 + 기호
            p.setPen(QColor(theme.FG))
            cx, cy = inner.center().x(), inner.center().y()
            p.drawLine(cx - 5, cy, cx + 5, cy)
            p.drawLine(cx, cy - 5, cx, cy + 5)
        p.setPen(QColor(theme.CONTROL_BORDER))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(inner, theme.R_SWATCH, theme.R_SWATCH)
        if self.isChecked():
            p.setPen(QColor(theme.ACCENT))
            p.drawRoundedRect(box.adjusted(0, 0, -1, -1), 8, 8)
        if self.hasFocus():
            p.setOpacity(1.0)
            theme.paint_focus_ring(p, self.rect(), theme.R_SWATCH + pad)


class ThumbRow(QWidget):
    """기본 배경 썸네일 + 파일에서 고르기."""

    changed = Signal(str)
    browse = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAccessibleName("배경 이미지")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self._thumbs: list[Thumb] = []
        for key, label in backgrounds.BUILTINS:
            th = Thumb(key, label)
            th.set_preview(_thumb_pixmap(key, Thumb.W, Thumb.H))
            th.clicked.connect(
                lambda _=False, k=key: self.changed.emit(backgrounds.PREFIX + k)
            )
            self.group.addButton(th)
            row.addWidget(th)
            self._thumbs.append(th)
        more = Thumb(None, "파일에서 고르기")
        more.clicked.connect(lambda: self.browse.emit())
        row.addWidget(more)
        row.addStretch(1)
        self._thumbs.append(more)

    def set_value(self, value: str) -> None:
        # SwatchRow 와 같은 함정 — 배타 그룹은 체크 해제를 무시한다.
        key = backgrounds.builtin_key(value) if backgrounds.is_builtin(value) else None
        self.group.setExclusive(False)
        for th in self._thumbs:
            if th.key is not None:
                th.setChecked(th.key == key)
        self.group.setExclusive(True)

    def set_enabled(self, on: bool, why: str = "") -> None:
        for th in self._thumbs:
            th.setEnabled(on)
        self.setToolTip("" if on else why)


def _thumb_pixmap(key: str, w: int, h: int) -> QPixmap:
    """배경 생성기가 만든 BGR 배열을 그대로 미리보기로 쓴다.

    썸네일을 따로 그리면 실제 배경과 달라져서 고른 것과 다른 게 나온다.
    """
    import numpy as np

    scale = 2  # 고DPI 에서 뭉개지지 않게 2배로 그린다
    bgr = np.ascontiguousarray(backgrounds.render(key, w * scale, h * scale))
    ih, iw = bgr.shape[:2]
    image = QImage(bgr.data, iw, ih, bgr.strides[0], QImage.Format.Format_BGR888)
    return QPixmap.fromImage(image.copy())  # copy: numpy 버퍼가 사라져도 살아 있게


# ----------------------------------------------------------------- 패널 --


def _section(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "section")
    return label


def _divider() -> QFrame:
    line = QFrame()
    line.setProperty("role", "divider")
    line.setFixedHeight(1)
    line.setFrameShape(QFrame.Shape.NoFrame)
    return line


class SettingsPanel(QWidget):
    def __init__(self, ctl, overlay):
        super().__init__(
            overlay,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.ctl = ctl
        self._closed_at = 0.0
        self._has_model = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # 뜰 때 포커스를 뺏지 않는다 — 방송 중에 쓰는 앱이다
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        # 창 자신은 Tab 정거장이 되면 안 된다 — 표시가 없어 포커스가 사라진
        # 것처럼 보인다. 컨트롤들이 StrongFocus 라 체인은 그대로 돌고, Esc 는
        # 처리 안 된 키가 부모로 올라오므로 여기서 계속 받는다.
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(GUTTER, GUTTER, GUTTER, GUTTER)
        card = QFrame()
        card.setObjectName("Card")
        card.setFixedWidth(PANEL_W)
        outer.addWidget(card)

        # 그림자는 **자식 프레임**에 건다. 최상위 투명 창에 걸면 렌더링이 깨진다.
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(theme.SHADOW_BLUR)
        shadow.setOffset(*theme.SHADOW_OFFSET)
        shadow.setColor(QColor(0, 0, 0, 115))
        card.setGraphicsEffect(shadow)

        # 화면이 짧은 노트북(768p 등)에서는 패널이 세로로 안 들어간다.
        # 스크롤 영역에 담아 두면 잘리는 대신 굴러간다.
        self._scroll = QScrollArea()
        # 스크롤 영역은 Qt 기본이 StrongFocus 라 Tab 첫 칸을 잡아먹는데,
        # 포커스 표시가 없어 "아무 데도 안 갔다"로 보인다. 컨트롤에 포커스가
        # 가면 Qt 가 알아서 그 위치로 스크롤하므로 이 정거장은 필요 없다.
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll.viewport().setAutoFillBackground(False)
        inner = QWidget()
        inner.setAutoFillBackground(False)
        self._scroll.setWidget(inner)
        QVBoxLayout(card).addWidget(self._scroll)
        card.layout().setContentsMargins(0, 0, 0, 0)

        body = QVBoxLayout(inner)
        body.setContentsMargins(16, 12, 16, 12)
        body.setSpacing(8)
        self._build(body)
        self._card = card
        self.sync()
        self._quality_timer = QTimer(self)
        self._quality_timer.setInterval(1000)
        self._quality_timer.timeout.connect(self._sync_quality_status)
        self._quality_timer.start()

    # ------------------------------------------------------------ 구성 --

    def _build(self, body: QVBoxLayout) -> None:
        ctl = self.ctl

        head = QHBoxLayout()
        title = QLabel("빠른 설정")
        title.setStyleSheet("font-size:13px; font-weight:600;")
        close = QPushButton("✕")
        # objectName 이 필요하다 — 일반 QPushButton 의 min-height 26px 가
        # setFixedSize 를 이겨서 24x24 로 지정해도 24x26 으로 그려진다.
        close.setObjectName("Close")
        close.setFixedSize(theme.CLOSE_BTN, theme.CLOSE_BTN)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        close.setToolTip("닫기 (Esc)")
        close.setAccessibleName("설정 패널 닫기")
        close.clicked.connect(self.hide)
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(close)
        body.addLayout(head)

        body.addWidget(_section("카메라 출력 모드"))
        self.camera_mode = QComboBox()
        self.camera_mode.setStyleSheet(
            "QComboBox { border: 2px solid " + theme.CONTROL_BORDER + "; padding: 4px; }"
            "QComboBox:focus { border: 2px solid " + theme.FOCUS + "; }")
        self.camera_mode.setAccessibleName("카메라 출력 모드 선택")
        self.camera_mode.currentIndexChanged.connect(
            lambda _: ctl.set_camera_mode(self.camera_mode.currentData()))
        body.addWidget(self.camera_mode)
        self.quality_status = QLabel()
        self.quality_status.setWordWrap(True)
        body.addWidget(self.quality_status)
        hint = QLabel("카메라가 지원한다고 알려준 해상도와 FPS 조합만 표시합니다. 변경하면 카메라를 다시 연결합니다. 인물 추적은 원본 일부를 잘라 확대합니다.")
        hint.setWordWrap(True)
        hint.setProperty("role", "hint")
        body.addWidget(hint)

        # ── 인물 추적 ──────────────────────────────────
        body.addWidget(_section("인물 추적"))
        self.track_missing = QLabel("모델 없음 — assets/selfie_seg.onnx")
        self.track_missing.setProperty("role", "danger")
        # 줄바꿈을 안 켜면 이 한 줄이 최소 폭을 뷰포트 너머로 밀어올려, 모델이
        # 없는 설치에서 패널 오른쪽 40px 가 통째로 잘린다 (가로 스크롤바는 꺼 둔
        # 상태라 복구도 안 된다). 긴 라벨에는 항상 켠다.
        self.track_missing.setWordWrap(True)
        body.addWidget(self.track_missing)

        row = QHBoxLayout()
        self.track_label = QLabel("사용")
        self.track_toggle = ToggleSwitch()
        self.track_toggle.setAccessibleName("인물 추적 사용")
        self.track_toggle.clicked.connect(
            lambda: ctl.set_track_enabled(self.track_toggle.isChecked())
        )
        row.addWidget(self.track_label)
        row.addStretch(1)
        row.addWidget(self.track_toggle)
        body.addLayout(row)

        self.track_hint = QLabel("얼굴을 따라 화면을 자동으로 잘라냅니다")
        self.track_hint.setProperty("role", "hint")
        body.addWidget(self.track_hint)

        self.speed = SliderRow(
            "속도", 1, 10, lambda v: "%d · %s" % (v, _word(v, SPEED_WORDS))
        )
        self.speed.changed.connect(ctl.set_track_speed)
        body.addWidget(self.speed)

        self.size_row = SliderRow(
            "크기", 1, 10, lambda v: "%d · %s" % (v, _word(v, SIZE_WORDS))
        )
        self.size_row.changed.connect(ctl.set_track_size)
        body.addWidget(self.size_row)

        body.addWidget(_divider())

        # ── 배경 ───────────────────────────────────────
        body.addWidget(_section("배경"))
        self.bg = Segmented(
            [("끄기", BG_OFF), ("투명", BG_TRANSPARENT), ("흐리게", BG_BLUR),
             ("단색", BG_COLOR), ("이미지", BG_IMAGE)],
            name="배경",
        )
        self.bg.changed.connect(ctl.set_bg_mode)
        body.addWidget(self.bg)

        self.blur = SliderRow("흐림 강도", 1, 50, lambda v: str(v))
        self.blur.changed.connect(ctl.set_bg_blur)
        body.addWidget(self.blur)

        self.bg_color = SwatchRow(
            "배경 색", ["#00b140", "#0047bb", "#000000", "#ffffff"]
        )
        self.bg_color.changed.connect(ctl.set_bg_color)
        self.bg_color.custom.connect(lambda: (ctl.pick_bg_color(), self.sync()))
        body.addWidget(self.bg_color)

        self.bg_image_label = QLabel("배경 이미지")
        body.addWidget(self.bg_image_label)
        self.bg_image = ThumbRow()
        self.bg_image.changed.connect(ctl.set_bg_image)
        self.bg_image.browse.connect(lambda: (ctl.pick_bg_image(), self.sync()))
        body.addWidget(self.bg_image)
        self.bg_image_name = QLabel("")
        self.bg_image_name.setProperty("role", "hint")
        self.bg_image_name.setWordWrap(True)
        body.addWidget(self.bg_image_name)

        body.addWidget(_divider())

        # ── 화면 ───────────────────────────────────────
        body.addWidget(_section("화면"))
        self.opacity = SliderRow("투명도", 20, 100, lambda v: "%d%%" % v, step=5)
        self.opacity.changed.connect(ctl.set_opacity)
        body.addWidget(self.opacity)

        self.border = SliderRow(
            "테두리 두께", 0, 8, lambda v: "없음" if v == 0 else "%dpx" % v
        )
        self.border.changed.connect(ctl.set_border_width)
        body.addWidget(self.border)

        self.border_color = SwatchRow(
            "테두리 색", ["#ffffff", "#000000", "#3ea6ff", "#5ce65c", "#ff5fa2"]
        )
        self.border_color.changed.connect(ctl.set_border_color)
        self.border_color.custom.connect(lambda: (ctl.pick_border_color(), self.sync()))
        body.addWidget(self.border_color)

        body.addWidget(_divider())

        # ── 보정 ───────────────────────────────────────
        body.addWidget(_section("보정"))
        self.smooth = SliderRow(
            "피부 매끄럽게", 0, 100, lambda v: "끔" if v == 0 else str(v)
        )
        self.smooth.changed.connect(ctl.set_skin_smooth)
        body.addWidget(self.smooth)

        self.tone = SliderRow("피부톤", -50, 50, lambda v: "%+d" % v if v else "0")
        self.tone.changed.connect(ctl.set_skin_tone)
        body.addWidget(self.tone)

        self.bright = SliderRow("밝기", -50, 50, lambda v: "%+d" % v if v else "0")
        self.bright.changed.connect(ctl.set_skin_bright)
        body.addWidget(self.bright)

        row = QHBoxLayout()
        row.addStretch(1)
        self.reset_retouch = QPushButton("보정 끄기")
        self.reset_retouch.setAccessibleName("피부 보정 초기화")
        self.reset_retouch.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.reset_retouch.clicked.connect(ctl.reset_retouch)
        row.addWidget(self.reset_retouch)
        body.addLayout(row)

        self.footer = QLabel()
        self.footer.setProperty("role", "hint")
        body.addWidget(self.footer)

    # ------------------------------------------------------------ 동기화 --

    def _sync_quality_status(self) -> None:
        if self.isVisible():
            self.quality_status.setText(self.ctl.camera_quality_status())

    def sync(self) -> None:
        """설정 -> 위젯. 신호를 막고 값만 넣어 되먹임을 피한다."""
        s = self.ctl.settings
        blocked = self.camera_mode.blockSignals(True)
        current = "%dx%d|%d|%s" % (
            s.camera_width, s.camera_height, s.camera_fps, s.camera_fourcc)
        modes = self.ctl.camera_modes()
        wanted_items = [
            ("%d×%d · %d FPS%s" % (
                mode["width"], mode["height"], mode["fps"],
                " · " + mode["fourcc"] if mode["fourcc"] else ""),
             "%dx%d|%d|%s" % (mode["width"], mode["height"],
                               mode["fps"], mode["fourcc"]))
            for mode in modes
        ]
        existing = [self.camera_mode.itemData(i) for i in range(self.camera_mode.count())]
        if existing != [data for _label, data in wanted_items]:
            self.camera_mode.clear()
            if wanted_items:
                for label, data in wanted_items:
                    self.camera_mode.addItem(label, data)
            else:
                self.camera_mode.addItem("자동 · 장치 기본 출력 사용", None)
                self.camera_mode.setEnabled(False)
        self.camera_mode.setEnabled(bool(wanted_items))
        self.camera_mode.setToolTip(
            "장치가 지원 모드를 알려 주지 않아 표준 목록을 대신 보여 줍니다. 고른 값을 장치가 못 맞추면 가장 가까운 모드로 열립니다."
            if wanted_items and self.ctl.camera_modes_are_default() else "")
        self.camera_mode.setCurrentIndex(self.camera_mode.findData(current))
        self.camera_mode.blockSignals(blocked)
        self.quality_status.setText(self.ctl.camera_quality_status())
        if self._has_model is None:  # 앱 수명 동안 안 바뀌는 값이라 한 번만 본다
            self._has_model = model_available()
        has_model = self._has_model

        self.track_missing.setVisible(not has_model)
        for w in (self.track_label, self.track_toggle, self.track_hint):
            w.setVisible(has_model)
            w.setEnabled(has_model)

        blocked = self.track_toggle.blockSignals(True)
        self.track_toggle.setChecked(s.track_enabled)
        self.track_toggle.blockSignals(blocked)

        self.speed.set_value(s.track_speed)
        self.size_row.set_value(s.track_size)
        live = has_model and s.track_enabled
        self.speed.set_enabled(live, "인물 추적을 먼저 켜세요")
        self.size_row.set_enabled(live, "인물 추적을 먼저 켜세요")

        self.bg.set_enabled_keys(
            {BG_OFF, BG_TRANSPARENT, BG_BLUR, BG_COLOR, BG_IMAGE}
            if has_model else {BG_OFF}
        )
        self.bg.set_value(s.bg_mode)
        self.blur.set_value(s.bg_blur)
        self.blur.set_enabled(
            s.bg_mode == BG_BLUR, "배경을 '흐리게'로 두면 조절할 수 있습니다"
        )
        self.bg_color.set_value(s.bg_color)
        self.bg_color.set_enabled(
            s.bg_mode == BG_COLOR, "배경을 '단색'으로 두면 조절할 수 있습니다"
        )
        on_image = s.bg_mode == BG_IMAGE
        self.bg_image.set_value(s.bg_image)
        self.bg_image.set_enabled(
            on_image, "배경을 '이미지'로 두면 조절할 수 있습니다"
        )
        self.bg_image_label.setEnabled(on_image)
        # 파일을 골랐을 때만 이름을 보여준다. 기본 배경은 썸네일이 곧 이름이다.
        custom = "" if backgrounds.is_builtin(s.bg_image) else os.path.basename(s.bg_image)
        self.bg_image_name.setText(custom)
        self.bg_image_name.setVisible(bool(custom))
        self.bg_image_name.setEnabled(on_image)

        self.opacity.set_value(s.opacity)
        shaped = s.shape != SHAPE_NONE
        self.border.set_value(s.border_width)
        self.border.set_enabled(shaped, "모양이 '없음'이면 테두리를 그리지 않습니다")
        self.border_color.set_value(s.border_color)
        self.border_color.set_enabled(
            shaped and s.border_width > 0, "두께가 0이면 색이 보이지 않습니다"
        )

        self.smooth.set_value(s.skin_smooth)
        self.tone.set_value(s.skin_tone)
        self.bright.set_value(s.skin_bright)
        self.reset_retouch.setEnabled(
            bool(s.skin_smooth or s.skin_tone or s.skin_bright)
        )

        self.footer.setText(self.ctl.diag_line())

    # ------------------------------------------------------------ 배치 --

    def reposition(self) -> None:
        overlay = self.parent()
        if overlay is None:
            return
        o = overlay.frameGeometry()
        screen = QGuiApplication.screenAt(o.center()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()

        # 내용이 화면보다 길면 카드를 줄이고 스크롤에 맡긴다
        # 카드에 1px 테두리가 있어 뷰포트는 그만큼 좁다. 안 더하면 정확히 2px
        # 넘쳐서 스크롤바가 늘 뜨고, 그 폭만큼 내용이 오른쪽으로 밀린다.
        wanted = self._scroll.widget().sizeHint().height() + CARD_BORDER * 2
        self._card.setFixedHeight(min(wanted, avail.height() - EDGE * 2 - GUTTER * 2))
        self.adjustSize()

        w, h = self.width() or PANEL_W + GUTTER * 2, self.height()

        # 오른쪽 -> 왼쪽 -> 아래 -> 위 순으로 들어가는 자리를 찾는다
        right = o.right() + GAP - GUTTER
        left = o.left() - GAP - w + GUTTER
        if right + w - GUTTER <= avail.right() - EDGE:
            x, y = right, o.top() - GUTTER
        elif left + GUTTER >= avail.left() + EDGE:
            x, y = left, o.top() - GUTTER
        else:
            x = o.center().x() - w // 2
            below = o.bottom() + GAP - GUTTER
            above = o.top() - GAP - h + GUTTER
            y = below if below + h - GUTTER <= avail.bottom() - EDGE else above

        x = max(avail.left() + EDGE - GUTTER, min(x, avail.right() - EDGE - w + GUTTER))
        y = max(avail.top() + EDGE - GUTTER, min(y, avail.bottom() - EDGE - h + GUTTER))
        self.move(QPoint(int(x), int(y)))

    # ------------------------------------------------------------ 표시 --

    def showEvent(self, event) -> None:
        self.sync()
        super().showEvent(event)
        self.reposition()
        QApplication.instance().installEventFilter(self)

    def hideEvent(self, event) -> None:
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._closed_at = time.monotonic()
        super().hideEvent(event)

    def focus_first_control(self) -> None:
        """키보드로 열었을 때 첫 컨트롤에 포커스를 준다.

        패널 자신은 NoFocus 라 activateWindow() 만으로는 갈 곳이 없다.
        """
        # 닫기 버튼은 건너뛴다 — 첫 칸에 두면 열자마자 Space 를 눌러 닫게 된다.
        for w in self.findChildren(QWidget):
            if (w.focusPolicy() != Qt.FocusPolicy.NoFocus
                    and w.isVisible() and w.isEnabled()
                    and w.objectName() != "Close"):
                w.setFocus(Qt.FocusReason.ShortcutFocusReason)
                return

    def just_closed(self, within: float = 0.4) -> bool:
        """방금 바깥클릭으로 닫혔는지. 우클릭 -> 메뉴 라벨 결정에 쓴다."""
        return (time.monotonic() - self._closed_at) < within

    def eventFilter(self, obj, event) -> bool:
        # 바깥을 누르면 닫는다. 다른 앱을 클릭할 때는 이벤트가 안 오므로
        # 방송 중 OBS 를 만져도 패널이 사라지지 않는다 — 의도한 동작이다.
        if event.type() != QEvent.Type.MouseButtonPress or not self.isVisible():
            return False
        # 가운데 버튼은 패널 토글 제스처다. 여기서 먼저 닫아 버리면 뒤이어
        # 오버레이 핸들러가 "닫혀 있네" 하고 다시 열어 버린다.
        if event.button() == Qt.MouseButton.MiddleButton:
            return False
        # 색상 대화상자 같은 모달이 떠 있으면 그 안의 클릭으로 닫지 않는다.
        if QApplication.activeModalWidget() is not None:
            return False
        # 부모 오버레이 위 클릭으로는 닫지 않는다. 닫아 버리면 패널을 연 채로
        # 창을 옮기거나 크기를 바꿀 수 없고, 오버레이를 따라 패널을 재배치하는
        # 코드(overlay.moveEvent -> reposition_panel)가 아예 도달하지 못한다.
        overlay = self.parent()
        if overlay is not None and overlay.frameGeometry().contains(
            event.globalPosition().toPoint()
        ):
            return False
        if not self.frameGeometry().contains(event.globalPosition().toPoint()):
            self.hide()
        return False

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)
