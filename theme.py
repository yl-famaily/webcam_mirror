"""다크 테마 — 색·치수 토큰과 QSS.

이 파일이 색과 간격의 **유일한 출처**다. 다른 모듈에서 hex 를 직접 쓰지 말 것.

명도 대비는 카드 배경(#1E1F24) 기준으로 맞췄다:
    FG 14.1:1 · FG_MUTED 6.3:1 · ACCENT 5.1:1 · KNOB 16.5:1
    CONTROL_BORDER 3.3:1 · FOCUS 7.9:1 (버튼 위 5.2:1) · MENU_HOVER 3.3:1
    FOCUS_ON_ACCENT 3.2:1 (선택된 버튼 위) · CONTROL_HOVER 글자 7.1:1
빈 groove(TRACK)만 1.52:1 로 낮은데, 이건 의도다 — 슬라이더의 상태를 알려주는
건 핸들과 채워진 구간이고 빈 홈은 장식이다. 3:1 로 올리면 채워진 쪽과
안 채워진 쪽이 구분되지 않아 오히려 못 쓰게 된다.

강조색 위의 글자는 FG 가 아니라 ON_ACCENT(순백)를 쓴다 — FG 로는 4.04:1 이라
본문 기준 4.5:1 에 미달한다.
"""

from __future__ import annotations

# ------------------------------------------------------------------- 색 --

BG_CARD = "#1E1F24"
BG_CARD_BORDER = "#34363E"
TRACK = "#3A3D46"
CONTROL_BORDER = "#6A6F7C"  # 미선택 버튼 경계 — 배경만으로는 1.5:1 이라 윤곽이 안 보인다
FG = "#ECEDF0"
FG_MUTED = "#9CA0AA"
FG_DISABLED = "#5A5E68"
ACCENT = "#4C8DFF"
ACCENT_HOVER = "#6BA1FF"
FOCUS = "#8AB4FF"  # 포커스 링
FOCUS_ON_ACCENT = "#FFFFFF"  # 강조색으로 칠해진 컨트롤 위의 포커스 링.
# FOCUS 는 ACCENT 와 1.53:1 이라 선택된 버튼 위에서는 그려져도 안 보인다.
# 배타 그룹의 Tab 정거장은 **항상 체크된 버튼**이라 키보드 사용자는 이 경우를
# 반드시 만난다.
ACCENT_PRESS = "#3A79E6"
DIVIDER = "#2C2E35"
DANGER = "#FF6B6B"
KNOB = "#FFFFFF"
ON_ACCENT = "#FFFFFF"  # 강조 배경 위 글자 — FG 로는 4.04:1 이라 4.5 에 못 미친다

MENU_BG = "#232429"
MENU_BORDER = "#35373F"
MENU_HOVER = "#3A6FD8"  # 메뉴 하이라이트 — 키보드 탐색의 유일한 표시라 3:1 확보
CONTROL_HOVER = "#4A4E59"  # 버튼/스크롤바 hover. **MENU_HOVER 를 쓰면 안 된다** —
# 파란 hover 가 파란 '선택됨'(ACCENT)과 1.48:1 이라 마우스만 얹어도 골라진
# 것처럼 보인다. 회색으로 두면 색상과 글자색(밝은 글자 vs 어두운 글자)
# 두 가지로 구분된다.

# ----------------------------------------------------------------- 치수 --

R_CARD = 12
R_CONTROL = 8
R_MENU = 8
R_SWATCH = 6

MENU_ITEM_H = 30  # WCAG 2.2 최소 타깃 24 를 넘기고 데스크톱에 과하지 않은 값
SLIDER_H = 24  # groove 전체가 클릭 영역이라 이 높이가 곧 히트 타깃
SLIDER_HANDLE = 18
SLIDER_GROOVE = 4
CLOSE_BTN = 28  # WCAG 2.2 SC 2.5.8 의 24 를 넘기고 제목줄 높이에도 맞는 값

SHADOW_BLUR = 28
SHADOW_OFFSET = (0, 6)
SHADOW_GUTTER = 16  # 그림자가 그려질 투명 여백

FONT_STACK = '"Segoe UI", "Malgun Gothic", sans-serif'


# ------------------------------------------------------------------ QSS --

# 슬라이더 핸들의 음수 마진이 핵심이다. groove 에 좌우 마진 9px 를 주고 핸들에
# 같은 값을 음수로 상쇄해야 핸들이 트랙 양 끝까지 간다. 세로 마진은
# -(핸들 - groove)/2 = -7.
_HANDLE_MARGIN_X = -SLIDER_HANDLE // 2
_HANDLE_MARGIN_Y = -(SLIDER_HANDLE - SLIDER_GROOVE) // 2
_GROOVE_MARGIN_X = SLIDER_HANDLE // 2
_CLOSE_INNER = CLOSE_BTN - 4  # QSS 높이는 테두리를 뺀 내용 기준이다

QSS = f"""
* {{ font-family: {FONT_STACK}; }}

/* ── 메뉴 ─────────────────────────────────────────────── */
QMenu {{
    background: {MENU_BG};
    border: 1px solid {MENU_BORDER};
    border-radius: {R_MENU}px;
    padding: 6px;
    color: {FG};
    font-size: 12px;
}}
QMenu::item {{
    height: {MENU_ITEM_H}px;
    padding: 0px 18px 0px 30px;
    border-radius: 6px;
}}
QMenu::item:selected {{ background: {MENU_HOVER}; color: {ON_ACCENT}; }}
QMenu::item:disabled {{ color: {FG_DISABLED}; }}
QMenu::separator {{
    height: 1px;
    background: {MENU_BORDER};
    margin: 6px 8px;
}}
/* indicator 에 image 를 지정하지 않는다 — 코드로 그린 pixmap 을 QSS 가 못 받고,
   잘못 건드리면 체크 표시가 통째로 사라진다. 크기만 잡고 그리기는 Fusion 에. */
QMenu::indicator {{ width: 16px; height: 16px; left: 8px; }}
QMenu::right-arrow {{ width: 10px; height: 10px; right: 10px; }}

/* ── 패널 ─────────────────────────────────────────────── */
QFrame#Card {{
    background: {BG_CARD};
    border: 1px solid {BG_CARD_BORDER};
    border-radius: {R_CARD}px;
}}
QLabel {{ color: {FG}; font-size: 12px; background: transparent; }}
QLabel[role="section"] {{
    color: {FG_MUTED};
    font-size: 11px;
    font-weight: 600;
    padding-top: 4px;
}}
QLabel[role="hint"] {{ color: {FG_MUTED}; font-size: 11px; }}
QLabel[role="value"] {{ color: {ACCENT}; font-size: 12px; font-weight: 600; }}
QLabel[role="danger"] {{ color: {DANGER}; font-size: 11px; }}
QLabel:disabled, QLabel[role="value"]:disabled {{ color: {FG_DISABLED}; }}

/* ── 슬라이더 ─────────────────────────────────────────── */
QSlider {{ height: {SLIDER_H}px; }}
QSlider::groove:horizontal {{
    height: {SLIDER_GROOVE}px;
    margin: 0px {_GROOVE_MARGIN_X}px;
    background: {TRACK};
    border-radius: {SLIDER_GROOVE // 2}px;
}}
QSlider::sub-page:horizontal {{
    height: {SLIDER_GROOVE}px;
    margin: 0px {_GROOVE_MARGIN_X}px;
    background: {ACCENT};
    border-radius: {SLIDER_GROOVE // 2}px;
}}
QSlider::add-page:horizontal {{
    height: {SLIDER_GROOVE}px;
    margin: 0px {_GROOVE_MARGIN_X}px;
    background: {TRACK};
    border-radius: {SLIDER_GROOVE // 2}px;
}}
QSlider::handle:horizontal {{
    width: {SLIDER_HANDLE}px;
    height: {SLIDER_HANDLE}px;
    margin: {_HANDLE_MARGIN_Y}px {_HANDLE_MARGIN_X}px;
    border-radius: {SLIDER_HANDLE // 2}px;
    background: {KNOB};
}}
QSlider::handle:horizontal:hover {{ background: {KNOB}; }}
QSlider::handle:horizontal:disabled {{ background: {FG_DISABLED}; }}
QSlider::sub-page:horizontal:disabled {{ background: {FG_DISABLED}; }}

/* ── 버튼 / 세그먼트 ──────────────────────────────────── */
QPushButton {{
    background: {TRACK};
    color: {FG};
    border: 1px solid {CONTROL_BORDER};
    border-radius: {R_CONTROL}px;
    padding: 0px 10px;
    min-height: 26px;
    font-size: 12px;
}}
/* 경계색은 건드리지 않는다 — hover 색으로 덮으면 3.27:1 이던 윤곽이
   1.98:1 로 떨어져 반려 사유였던 `border: none` 상태로 되돌아간다. */
QPushButton:hover {{ background: {CONTROL_HOVER}; }}
QPushButton:pressed {{ background: {ACCENT_PRESS}; border-color: {ACCENT_PRESS};
                       color: #0F1115; }}  /* 글자색을 안 주면 상속돼 3.55:1 */
QPushButton:checked {{ background: {ACCENT}; border-color: {ACCENT};
                       color: #0F1115; font-weight: 600; }}
QPushButton:disabled {{ background: {DIVIDER}; border-color: {DIVIDER};
                        color: {FG_DISABLED}; }}

/* 포커스 표시. 이게 없으면 화살표 키가 어느 컨트롤에 가는지 알 수 없다.
   QSlider / ToggleSwitch / Swatch 는 여기가 아니라 paintEvent 에서 직접 그린다 —
   QSS 테두리가 내용 사각형을 줄여 groove 기하를 밀어버리고, 커스텀 paintEvent 는
   애초에 QSS 박스를 안 그려서 규칙이 도달하지도 않는다. */
/* 세그먼트 줄은 버튼이 여럿이라 기본 좌우 여백(10px)이면 카드 폭을 넘긴다.
   배경 모드가 4개에서 5개(이미지)로 늘면서 실제로 넘쳤다. */
QPushButton[seg="true"] {{ padding: 0px 3px; }}

QPushButton:checked:focus, QPushButton:pressed:focus {{
    border-color: {FOCUS_ON_ACCENT};
}}
QPushButton:focus {{
    border: 2px solid {FOCUS};
    min-height: 24px;   /* 테두리가 1->2 로 굵어진 만큼 내용 높이를 줄여 */
    padding: 0px 9px;   /* 총 크기를 고정한다. 안 그러면 Tab 마다 튄다. */
}}
/* 테두리를 항상 2px 로 잡고 색만 바꾼다 — 포커스가 와도 크기가 안 변한다.
   높이는 테두리를 뺀 내용 기준이라 CLOSE_BTN - 4 를 준다. */
QPushButton#Close {{
    min-height: {_CLOSE_INNER}px;
    max-height: {_CLOSE_INNER}px;
    min-width: {_CLOSE_INNER}px;
    padding: 0px;
    font-size: 13px;
    color: {FG_MUTED};
    background: transparent;
    border: 2px solid transparent;
    border-radius: {R_CONTROL}px;
}}
QPushButton#Close:hover {{ background: {DANGER}; color: {BG_CARD}; }}  /* 흰 글자는 2.78:1 */
QPushButton#Close:focus {{ border-color: {FOCUS}; min-height: {_CLOSE_INNER}px; padding: 0px; }}

QFrame[role="divider"] {{ background: {DIVIDER}; max-height: 1px; border: none; }}

QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 4px 2px;
}}
QScrollBar::handle:vertical {{
    background: {TRACK};
    border-radius: 3px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: {CONTROL_HOVER}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}

QToolTip {{
    background: {MENU_BG};
    color: {FG};
    border: 1px solid {MENU_BORDER};
    padding: 4px 8px;
}}
"""


FOCUS_RING = 2  # 포커스 링 두께
FOCUS_PAD = 3  # 링을 그릴 여백. 커스텀 위젯은 이만큼 크게 잡는다


def paint_focus_ring(painter, rect, radius: int) -> None:
    """직접 그리는 컨트롤의 포커스 링.

    QSS `:focus` 는 두 경우에 못 쓴다. paintEvent 에서 `super()` 를 안 부르는
    위젯은 QSS 박스 자체를 안 그려서 규칙이 도달하지 않고, QSlider 는 테두리가
    내용 사각형을 줄여 groove 가 1px 밀린다. 그래서 여기로 통일한다.
    """
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QPen

    half = FOCUS_RING / 2.0
    pen = QPen(QColor(FOCUS))
    pen.setWidth(FOCUS_RING)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(
        QRectF(rect).adjusted(half, half, -half, -half), radius, radius
    )


def apply_theme(app) -> None:
    """위젯을 만들기 **전에** 부른다.

    Fusion 으로 먼저 바꾸는 게 중요하다 — Windows 기본 스타일은 네이티브
    렌더러라 QMenu 의 높이·여백 같은 QSS 상당수를 그냥 무시한다.
    """
    from PySide6.QtWidgets import QStyleFactory

    style = QStyleFactory.create("Fusion")
    if style is not None:
        app.setStyle(style)
    app.setStyleSheet(QSS)


def style_menu(menu) -> None:
    """QMenu 에 둥근 모서리를 주면 Windows 에서 네 귀퉁이에 검은 사각형이 남는다.
    네이티브 그림자가 사각형 기준으로 그려지기 때문이다. 프레임리스 + 그림자
    끄기 + 투명 배경 3콤보로 없앤다. **첫 popup 전에** 호출해야 한다.

    QMenu 에는 QGraphicsDropShadowEffect 를 대신 걸 자식 프레임이 없으므로,
    분리 수단은 1px 테두리다.
    """
    from PySide6.QtCore import Qt

    menu.setWindowFlags(
        menu.windowFlags()
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.NoDropShadowWindowHint
    )
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
