"""Partner Center 의 **스토어 등재 페이지**에 올릴 이미지를 만든다.

    python make_listing_assets.py

`make_store_assets.py` 와 다른 것이다. 그쪽은 패키지 **안에** 들어가 시작 메뉴
타일과 앱 목록에 쓰이고, 이쪽은 패키지 밖에서 스토어 상품 페이지에 쓰인다.

**글자를 넣지 않는다.** 스토어 이미지는 언어별로 따로 올릴 수 있지만, 여기에
문구를 그려 넣으면 4개 언어분을 따로 만들고 문구가 바뀔 때마다 다시 만들어야
한다. 제품명 "Webcam Mirror" 는 네 언어에서 모두 같으므로 워드마크만 넣어
한 벌로 네 언어를 함께 쓴다.

바탕은 앱의 다크 테마(theme.BG_CARD 계열)를 따른다. 아이콘이 파란색이라
파란 바탕에 올리면 대비가 죽고, 흰 바탕은 스토어의 라이트/다크 어느 쪽에서도
카드 경계가 사라진다.
"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPixmap,
)

import theme
from tray import draw_icon_pixmap

OUT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "packaging", "StoreListing"
)

WORDMARK = "Webcam Mirror"

# Partner Center 가 받는 규격. 1:1 300 은 예전부터 쓰던 필수 로고이고, 나머지는
# 스토어가 자리에 맞춰 골라 쓴다 — 넣어 두면 검색 결과·추천 카드에서 잘린
# 이미지 대신 제대로 된 게 나온다.
SPECS = (
    # (파일명, 폭, 높이, 워드마크 표시)
    ("StoreLogo-300x300.png", 300, 300, False),
    ("BoxArt-1x1-1080.png", 1080, 1080, False),
    ("BoxArt-1x1-2160.png", 2160, 2160, False),
    ("Poster-9x16-720x1080.png", 720, 1080, True),
    ("Hero-16x9-1920x1080.png", 1920, 1080, True),
    ("Hero-16x9-2400x1200.png", 2400, 1200, True),
)


def _font(px: int) -> QFont:
    # 워드마크는 라틴 문자뿐이라 CJK 폰트가 없어도 안전하다.
    # 단, QT_QPA_PLATFORM=offscreen 에서는 폰트 DB 가 비어(패밀리 0개) 글자가
    # 통째로 두부로 나온다. 그래서 이 스크립트는 offscreen 을 쓰지 않는다.
    font = QFont("Segoe UI", -1, QFont.Weight.DemiBold)
    font.setPixelSize(px)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def _render(width: int, height: int, wordmark: bool) -> QPixmap:
    canvas = QPixmap(width, height)
    p = QPainter(canvas)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    # 바탕 — 위가 살짝 밝은 세로 그라디언트. 평평한 단색은 큰 화면에서 인쇄물
    # 처럼 죽어 보인다.
    grad = QLinearGradient(0.0, 0.0, 0.0, float(height))
    grad.setColorAt(0.0, QColor("#26282F"))
    grad.setColorAt(1.0, QColor(theme.BG_CARD))
    p.fillRect(0, 0, width, height, grad)

    short = min(width, height)
    if not wordmark:
        icon_px = int(short * 0.62)
        p.drawPixmap(
            (width - icon_px) // 2, (height - icon_px) // 2, draw_icon_pixmap(icon_px)
        )
    elif width >= height:
        # 가로형 — 아이콘과 워드마크를 나란히 두고 묶음을 가운데 정렬한다.
        icon_px = int(height * 0.42)
        text_px = int(height * 0.15)
        p.setFont(_font(text_px))
        text_w = p.fontMetrics().horizontalAdvance(WORDMARK)
        gap = int(icon_px * 0.34)
        total = icon_px + gap + text_w
        # 음수가 되면 아이콘이 왼쪽으로 잘려 나간다. 폰트가 예상보다 넓어도
        # 캔버스 안에 머물게 한다.
        x = max(int(width * 0.04), (width - total) // 2)
        p.drawPixmap(x, (height - icon_px) // 2, draw_icon_pixmap(icon_px))
        p.setPen(QColor(theme.FG))
        p.drawText(
            QRectF(x + icon_px + gap, 0.0, float(text_w), float(height)),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            WORDMARK,
        )
    else:
        # 세로형 — 아이콘 아래에 워드마크.
        icon_px = int(width * 0.52)
        text_px = int(width * 0.11)
        gap = int(icon_px * 0.26)
        block = icon_px + gap + text_px
        top = (height - block) // 2
        p.drawPixmap((width - icon_px) // 2, top, draw_icon_pixmap(icon_px))
        p.setFont(_font(text_px))
        p.setPen(QColor(theme.FG))
        p.drawText(
            QRectF(0.0, float(top + icon_px + gap), float(width), float(text_px * 1.6)),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
            WORDMARK,
        )

    p.end()
    return canvas


def main() -> int:
    # offscreen 을 쓰면 안 된다 — 위 _font() 의 주석 참고. 기본(windows)
    # 플랫폼은 창을 띄우지 않고도 QPixmap 렌더링이 된다.
    app = QGuiApplication(sys.argv)
    os.makedirs(OUT_DIR, exist_ok=True)

    # 글리프가 없으면 조용히 두부가 박힌 이미지가 나온다. 만들기 전에 막는다.
    if not QFontMetrics(_font(100)).inFont("W"):
        print("listing: FAILED 워드마크를 그릴 폰트가 없습니다")
        del app
        return 1

    total = 0
    try:
        for name, w, h, wordmark in SPECS:
            path = os.path.join(OUT_DIR, name)
            if not _render(w, h, wordmark).save(path, "PNG"):
                raise RuntimeError("PNG 저장 실패: %s" % path)
            size = os.path.getsize(path)
            total += size
            print("  %-28s %5dx%-5d %6.1f KB" % (name, w, h, size / 1024))
    except (OSError, RuntimeError) as exc:
        print("listing: FAILED %s" % exc)
        return 1
    finally:
        del app

    print("listing: %s (%d files, %.1f KB)" % (OUT_DIR, len(SPECS), total / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
