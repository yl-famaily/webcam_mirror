"""시스템 트레이 아이콘. 아이콘은 코드로 그려서 바이너리 에셋을 두지 않는다."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

import theme
from menu import rebuild_into


ICON_SIZES = (16, 20, 24, 32, 48, 64, 128, 256)

# 그림 = 앱의 실제 출력물, 즉 "버블 속 인물". 둥근 사각형이라 트레이에 흔한
# 원형 아이콘들과 형태만으로 구분된다 (예전 "파란 원 + 흰 점"은 라디오버튼으로
# 읽혔다).
#
# 색은 대비를 재고 골랐다. 예전 #2b6cf6 은 다크 작업표시줄에서 3.55:1 로
# 아슬아슬했다. 지금 조합은 밝은 쪽이 다크에서 4.43:1, 어두운 쪽이 라이트에서
# 4.66:1 이라 어느 테마에서도 WCAG 1.4.11 의 3:1 을 여유 있게 넘는다.
BLUE_LIGHT = "#3B82F6"
BLUE_DARK = "#2563EB"

RADIUS = 0.28  # 바탕 모서리 (S 배수)
RADIUS_SMALL = 0.25  # 16px 이하 — 0.28 은 4.5px 라 반픽셀에서 뭉갠다
HEAD_R = 0.132
HEAD_CY = 0.355
SHOULDER_RX = 0.290
SHOULDER_RY = 0.300
GAP = 0.058  # 머리와 어깨 사이 — 이 틈이 사라지면 눈사람이 아니라 얼룩이 된다


def _snap(value: float) -> float:
    return float(round(value))


def draw_icon_pixmap(size: int) -> QPixmap:
    """한 사이즈의 아이콘을 그린다. make_icon() 과 make_icon.py 가 공유한다.

    작은 사이즈에서 안티에일리어싱을 **끄지 않는다.** 인물 실루엣은 곡선이라
    AA 를 끄면 계단이 생겨 오히려 못 알아본다. 대신 좌표를 정수 픽셀에 스냅해
    가장자리가 반픽셀에 걸치지 않게 한다.
    """
    s = float(size)
    tiny = size <= 24  # 이 아래로는 그라디언트·하이라이트가 1~2 픽셀에 뭉갠다

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)

    radius = s * (RADIUS_SMALL if size <= 16 else RADIUS)
    base = QPainterPath()
    base.addRoundedRect(QRectF(0.0, 0.0, s, s), radius, radius)

    if tiny:
        p.fillPath(base, QColor(BLUE_LIGHT))
    else:
        gradient = QLinearGradient(0.0, 0.0, s, s)
        gradient.setColorAt(0.0, QColor(BLUE_LIGHT))
        gradient.setColorAt(1.0, QColor(BLUE_DARK))
        p.fillPath(base, QBrush(gradient))
        # 위쪽 광택. 없어도 되지만 48px 이상에서 평평해 보이는 걸 막는다.
        sheen = QLinearGradient(0.0, 0.0, 0.0, s * 0.55)
        sheen.setColorAt(0.0, QColor(255, 255, 255, 46))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillPath(base, QBrush(sheen))

    # ── 인물 ────────────────────────────────────────────────
    head_r = s * HEAD_R
    head_cy = s * HEAD_CY
    gap = s * GAP
    if tiny:
        # 지름과 위 가장자리를 정수로 맞춘다. 16px 에서는 틈을 정확히 1픽셀로
        # 강제해야 머리와 어깨가 붙어 하나의 덩어리로 뭉치지 않는다.
        # 반지름을 정수로 맞춘다. 캔버스가 짝수라 중심은 정수이고, 반지름까지
        # 정수여야 좌우 가장자리가 반픽셀에 안 걸린다.
        head_r = max(1.0, _snap(head_r))
        head_cy = _snap(head_cy - head_r) + head_r
        gap = 1.0 if size <= 16 else max(1.0, _snap(gap))

    person = QPainterPath()
    person.addEllipse(QRectF(s * 0.5 - head_r, head_cy - head_r, head_r * 2, head_r * 2))

    shoulder_rx = s * SHOULDER_RX
    shoulder_ry = s * SHOULDER_RY
    shoulder_top = head_cy + head_r + gap
    if tiny:
        shoulder_rx = max(2.0, _snap(shoulder_rx))
        shoulder_top = _snap(shoulder_top)
    shoulder = QPainterPath()
    shoulder.addEllipse(
        QRectF(s * 0.5 - shoulder_rx, shoulder_top, shoulder_rx * 2, shoulder_ry * 2)
    )
    person.addPath(shoulder)

    # 어깨 타원은 바탕 아래로 삐져나간다. 바탕과 교집합을 취해 잘라낸다 —
    # 안 하면 둥근 모서리 밖에 흰 조각이 남는다.
    p.fillPath(person.simplified().intersected(base), QColor("#FFFFFF"))
    p.end()
    return pixmap


def make_icon() -> QIcon:
    """앱이 쓰는 유일한 아이콘 진입점 — 트레이·창·알림이 전부 여기서 나온다."""
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(draw_icon_pixmap(size))
    return icon


class TrayIcon(QSystemTrayIcon):
    def __init__(self, ctl):
        super().__init__(make_icon())
        self.ctl = ctl
        from main import APP_VERSION

        self.setToolTip("Webcam Mirror %s" % APP_VERSION)

        self._menu = QMenu()
        theme.style_menu(self._menu)
        # 트레이 메뉴는 열릴 때마다 현재 설정 상태로 다시 구성한다.
        self._menu.aboutToShow.connect(self._rebuild)
        self.setContextMenu(self._menu)

        self.activated.connect(self._on_activated)

    def _rebuild(self) -> None:
        rebuild_into(self._menu, self.ctl)

    def _on_activated(self, reason) -> None:
        # Trigger 만 본다. Windows 는 더블클릭 때 Trigger 를 먼저 보내므로
        # DoubleClick 까지 받으면 숨김 -> 표시로 두 번 토글돼 제자리로 돌아온다.
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.ctl.toggle_visible()
