"""우클릭 메뉴와 트레이 메뉴가 공유하는 메뉴 구성."""

from __future__ import annotations

import os

import backgrounds

from PySide6.QtGui import QActionGroup, QColor
from PySide6.QtWidgets import QColorDialog, QMenu

import theme
from effects import (
    BLUR_LEVELS,
    BRIGHT_LEVELS,
    SMOOTH_LEVELS,
    TONE_LEVELS,
    model_available,
)
from settings import (
    ASPECTS,
    BG_BLUR,
    BG_COLOR,
    BG_IMAGE,
    BG_OFF,
    BG_TRANSPARENT,
    SHAPE_ELLIPSE,
    SHAPE_NONE,
    SHAPE_RECT,
    SIZE_STEPS,
    size_for,
)

BG_COLORS = [
    ("초록 (크로마키)", "#00b140"),
    ("파랑", "#0047bb"),
    ("검정", "#000000"),
    ("흰색", "#ffffff"),
]
OPACITIES = [100, 75, 50, 25]
BORDERS = [("없음", 0), ("얇게 (1px)", 1), ("보통 (2px)", 2), ("두껍게 (4px)", 4)]
COLORS = [
    ("흰색", "#ffffff"),
    ("검정", "#000000"),
    ("하늘", "#3ea6ff"),
    ("연두", "#5ce65c"),
    ("분홍", "#ff5fa2"),
]


def _submenu(menu: QMenu, title: str) -> QMenu:
    """서브메뉴도 최상위와 같은 처리를 받아야 한다.

    `addMenu()` 로 만든 메뉴는 별개의 최상위 팝업 창이라 부모의 프레임리스/
    투명 설정을 물려받지 않는다. 빼먹으면 서브메뉴만 각진 모서리에 네이티브
    그림자가 남는데, 항목의 대부분이 서브메뉴 안에 있어 개선이 거의 안 보인다.
    """
    sub = menu.addMenu(title)
    theme.style_menu(sub)
    return sub


def _radio(menu: QMenu, group: QActionGroup, label: str, checked: bool, slot):
    action = menu.addAction(label)
    action.setCheckable(True)
    action.setChecked(checked)
    group.addAction(action)
    action.triggered.connect(slot)
    return action


def _toggle(menu: QMenu, label: str, checked: bool, slot):
    action = menu.addAction(label)
    action.setCheckable(True)
    action.setChecked(checked)
    action.triggered.connect(slot)
    return action


def populate_menu(menu: QMenu, ctl) -> QMenu:
    """이미 만들어진 QMenu 를 현재 설정 상태에 맞춰 채운다."""
    s = ctl.settings

    # 라벨과 동작을 **같은 판단**으로 묶는다. toggle_panel() 을 걸면, 우클릭
    # 시점에 바깥클릭 필터가 이미 패널을 닫아 둔 탓에 "설정 닫기" 를 눌렀는데
    # 다시 열린다. opened 가 정답을 들고 있으므로 그걸로 동작까지 정한다.
    opened = ctl.panel_was_open()
    entry = menu.addAction(
        "설정 닫기	Ctrl+Alt+S" if opened else "설정 열기…	Ctrl+Alt+S"
    )
    entry.triggered.connect(
        lambda *_: ctl.close_panel() if opened else ctl.open_panel()
    )
    menu.addSeparator()

    # -- 모양 ---------------------------------------------------------- #
    shape_menu = _submenu(menu, "모양")
    shape_group = QActionGroup(shape_menu)
    shape_group.setExclusive(True)
    _radio(
        shape_menu, shape_group, "원형 / 타원", s.shape == SHAPE_ELLIPSE,
        lambda *_: ctl.set_shape(SHAPE_ELLIPSE),
    )
    _radio(
        shape_menu, shape_group, "사각형", s.shape == SHAPE_RECT,
        lambda *_: ctl.set_shape(SHAPE_RECT),
    )
    _radio(
        shape_menu, shape_group, "없음 (전체)", s.shape == SHAPE_NONE,
        lambda *_: ctl.set_shape(SHAPE_NONE),
    )

    # -- 비율 ---------------------------------------------------------- #
    aspect_menu = _submenu(menu, "비율")
    aspect_group = QActionGroup(aspect_menu)
    aspect_group.setExclusive(True)
    for key, label, _ratio in ASPECTS:
        _radio(
            aspect_menu, aspect_group, label, s.aspect == key,
            lambda *_, k=key: ctl.set_aspect(k),
        )
    aspect_menu.addSeparator()
    keep = _toggle(
        aspect_menu, "현재 비율 유지 (드래그 중 Shift 와 동일)", s.lock_aspect,
        lambda checked=False: ctl.set_lock_aspect(bool(checked)),
    )
    keep.setEnabled(s.aspect == "free")  # 비율을 고른 상태면 의미가 없다

    # -- 크기 ---------------------------------------------------------- #
    # 긴 변 프리셋을 현재 비율에 적용한다 — 비율이 몇 개든 크기 선택지가 생긴다.
    size_menu = _submenu(menu, "크기")
    for label, long_edge in SIZE_STEPS:
        width, height = size_for(s.aspect, long_edge, s.w, s.h)
        action = size_menu.addAction("%s	%d x %d" % (label, width, height))
        action.setCheckable(True)
        action.setChecked(s.w == width and s.h == height)
        action.triggered.connect(lambda *_, w=width, h=height: ctl.set_size(w, h))
    size_menu.addSeparator()
    size_menu.addAction("위치 / 크기 초기화").triggered.connect(
        lambda *_: ctl.reset_geometry()
    )

    # -- 테두리 -------------------------------------------------------- #
    border_menu = _submenu(menu, "테두리")
    width_group = QActionGroup(border_menu)
    width_group.setExclusive(True)
    for label, value in BORDERS:
        _radio(
            border_menu, width_group, label, s.border_width == value,
            lambda *_, v=value: ctl.set_border_width(v),
        )
    border_menu.addSeparator()
    color_group = QActionGroup(border_menu)
    color_group.setExclusive(True)
    current = s.border_color.lower()
    for label, value in COLORS:
        _radio(
            border_menu, color_group, label, current == value,
            lambda *_, v=value: ctl.set_border_color(v),
        )
    border_menu.addAction("직접 선택...").triggered.connect(
        lambda *_: ctl.pick_border_color()
    )

    # -- 투명도 -------------------------------------------------------- #
    opacity_menu = _submenu(menu, "투명도")
    opacity_group = QActionGroup(opacity_menu)
    opacity_group.setExclusive(True)
    for value in OPACITIES:
        _radio(
            opacity_menu, opacity_group, "%d%%" % value, s.opacity == value,
            lambda *_, v=value: ctl.set_opacity(v),
        )

    # -- 보정 ---------------------------------------------------------- #
    retouch_menu = _submenu(menu, "보정")
    for title, levels, current, setter in (
        ("피부 매끄럽게", SMOOTH_LEVELS, s.skin_smooth, ctl.set_skin_smooth),
        ("피부톤", TONE_LEVELS, s.skin_tone, ctl.set_skin_tone),
        ("밝기", BRIGHT_LEVELS, s.skin_bright, ctl.set_skin_bright),
    ):
        sub = _submenu(retouch_menu, title)
        group = QActionGroup(sub)
        group.setExclusive(True)
        for label, value in levels:
            _radio(
                sub, group, label, current == value,
                lambda *_, v=value, fn=setter: fn(v),
            )
    retouch_menu.addSeparator()
    reset = retouch_menu.addAction("보정 끄기")
    reset.setEnabled(bool(s.skin_smooth or s.skin_tone or s.skin_bright))
    reset.triggered.connect(lambda *_: ctl.reset_retouch())

    # -- 추적 ---------------------------------------------------------- #
    track_menu = _submenu(menu, "인물 추적")
    if not model_available():
        na = track_menu.addAction("모델 없음 — assets/selfie_seg.onnx")
        na.setEnabled(False)
    else:
        # 속도·크기는 슬라이더가 있는 설정 패널에서만 조절한다. 여기에 예전
        # 4단계 라디오를 남겨 두면 같은 기능에 UI 가 둘이 되고, 메뉴 쪽에서는
        # 속도를 아예 못 바꾼다.
        _toggle(
            track_menu, "인물 추적 사용", s.track_enabled,
            lambda checked=False: ctl.set_track_enabled(bool(checked)),
        )
        track_menu.addSeparator()
        detail = track_menu.addAction(
            "속도 %d · 크기 %d — 설정에서 조절" % (s.track_speed, s.track_size)
        )
        detail.setEnabled(False)
        track_menu.addAction("설정 열기…").triggered.connect(
            lambda *_: ctl.open_panel()
        )

    # -- 배경 ---------------------------------------------------------- #
    bg_menu = _submenu(menu, "배경")
    if not model_available():
        missing = bg_menu.addAction("모델 없음 — assets/selfie_seg.onnx")
        missing.setEnabled(False)
    else:
        bg_group = QActionGroup(bg_menu)
        bg_group.setExclusive(True)
        for label, value in (
            ("끄기", BG_OFF),
            ("투명 (바탕화면 비침)", BG_TRANSPARENT),
            ("흐리게", BG_BLUR),
            ("단색", BG_COLOR),
            ("이미지", BG_IMAGE),
        ):
            _radio(
                bg_menu, bg_group, label, s.bg_mode == value,
                lambda *_, v=value: ctl.set_bg_mode(v),
            )
        bg_menu.addSeparator()

        blur_sub = _submenu(bg_menu, "흐림 강도")
        blur_sub.setEnabled(s.bg_mode == BG_BLUR)
        blur_group = QActionGroup(blur_sub)
        blur_group.setExclusive(True)
        for label, value in BLUR_LEVELS:
            _radio(
                blur_sub, blur_group, label, s.bg_blur == value,
                lambda *_, v=value: ctl.set_bg_blur(v),
            )

        color_sub = _submenu(bg_menu, "배경 색")
        color_sub.setEnabled(s.bg_mode == BG_COLOR)
        bgcolor_group = QActionGroup(color_sub)
        bgcolor_group.setExclusive(True)
        current_bg = s.bg_color.lower()
        for label, value in BG_COLORS:
            _radio(
                color_sub, bgcolor_group, label, current_bg == value,
                lambda *_, v=value: ctl.set_bg_color(v),
            )
        image_sub = _submenu(bg_menu, "배경 이미지")
        image_sub.setEnabled(s.bg_mode == BG_IMAGE)
        image_group = QActionGroup(image_sub)
        image_group.setExclusive(True)
        for key, label in backgrounds.BUILTINS:
            _radio(
                image_sub, image_group, label,
                s.bg_image == backgrounds.PREFIX + key,
                lambda *_, k=key: ctl.set_bg_image(backgrounds.PREFIX + k),
            )
        image_sub.addSeparator()
        image_sub.addAction("파일에서 고르기...").triggered.connect(
            lambda *_: ctl.pick_bg_image()
        )

        color_sub.addAction("직접 선택...").triggered.connect(
            lambda *_: ctl.pick_bg_color()
        )

    # -- 카메라 -------------------------------------------------------- #
    camera_menu = _submenu(menu, "카메라")
    cameras = ctl.cameras()
    if not cameras:
        action = camera_menu.addAction("장치를 찾을 수 없음")
        action.setEnabled(False)
    else:
        camera_group = QActionGroup(camera_menu)
        camera_group.setExclusive(True)
        for index, name in cameras:
            _radio(
                camera_menu, camera_group, "%d. %s" % (index, name),
                s.camera_index == index,
                lambda *_, i=index, n=name: ctl.switch_camera(i, n),
            )
    camera_menu.addSeparator()
    camera_menu.addAction("다시 연결").triggered.connect(
        lambda *_: ctl.restart_camera()
    )
    camera_menu.addAction("장치 다시 검색").triggered.connect(
        lambda *_: ctl.cameras(force=True)
    )

    menu.addSeparator()

    # -- 토글 ---------------------------------------------------------- #
    _toggle(
        menu, "좌우 반전 (거울)	Ctrl+Alt+F", s.mirror,
        lambda checked=False: ctl.set_mirror(bool(checked)),
    )
    _toggle(
        menu, "항상 맨 위	Ctrl+Alt+T", s.always_on_top,
        lambda checked=False: ctl.set_always_on_top(bool(checked)),
    )
    _toggle(
        menu, "클릭 통과	Ctrl+Alt+C", s.click_through,
        lambda checked=False: ctl.set_click_through(bool(checked)),
    )
    _toggle(
        menu, "창 숨기기	Ctrl+Alt+M", not ctl.window.isVisible(),
        lambda checked=False: ctl.set_hidden(bool(checked)),
    )

    menu.addSeparator()
    menu.addAction("종료").triggered.connect(lambda *_: ctl.quit())

    # 진단용 한 줄 — 어느 추론 백엔드를 쓰는지 눈으로 확인할 수 있어야 한다.
    # onnxruntime 이 조용히 cv2.dnn 으로 떨어져도 알아챌 방법이 없으면 곤란하다.
    menu.addSeparator()
    info = menu.addAction("v%s · 추론 %s" % (ctl.version, ctl.backend))
    info.setEnabled(False)
    return menu


def build_menu(ctl, parent=None) -> QMenu:
    menu = QMenu(parent)
    theme.style_menu(menu)
    return populate_menu(menu, ctl)


def rebuild_into(menu: QMenu, ctl) -> None:
    """이미 있는 메뉴를 비우고 현재 설정 상태로 다시 채운다.

    `clear()` 는 액션만 지우고 `addMenu()` 로 만든 서브메뉴는 남기므로,
    직접 지워주지 않으면 열 때마다 메뉴 객체가 쌓인다.
    """
    if menu.isVisible():
        return  # 열려 있는 메뉴를 재구성하면 사용자가 보던 항목이 사라진다
    for submenu in menu.findChildren(QMenu):
        submenu.setParent(None)
        submenu.deleteLater()
    menu.clear()
    populate_menu(menu, ctl)


def ask_color(parent, initial: str, title: str = "색상 선택") -> str | None:
    color = QColorDialog.getColor(QColor(initial), parent, title)
    return color.name() if color.isValid() else None


def ask_image(parent, initial: str = "") -> str | None:
    """배경으로 쓸 이미지 파일을 고른다."""
    from PySide6.QtWidgets import QFileDialog

    start = initial if initial and os.path.isfile(initial) else ""
    path, _ = QFileDialog.getOpenFileName(
        parent, "배경 이미지 선택", start,
        "이미지 (*.png *.jpg *.jpeg *.bmp *.webp);;모든 파일 (*.*)",
    )
    return path or None
