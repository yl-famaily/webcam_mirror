"""MSIX(Store) 패키지가 요구하는 타일·로고 PNG 를 만든다.

    python make_store_assets.py

`tray.draw_icon_pixmap` 을 그대로 쓴다 — 트레이·창·exe 아이콘과 같은 그림이
스토어 타일에도 나가야 하기 때문이다. 아이콘 자체가 모서리 둥근 "바탕 + 인물"
이라 타일을 꽉 채우면 Windows 가 타일 뒤에 깔아 주는 배경색(BackgroundColor)
위에서 색 덩어리로만 보인다. 그래서 타일 계열은 여백을 두고 가운데 놓는다.

스케일 변형(scale-100/125/150/200/400)은 Windows 가 DPI 에 따라 고르는 것이라
파일명 규칙(`Name.scale-200.png`)을 지켜 미리 만들어 둔다. 없으면 한 장을
늘려 쓰느라 뭉갠다.
"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import QApplication  # noqa: F401  (QPixmap 용 플랫폼 초기화)

from tray import draw_icon_pixmap

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "packaging", "Assets")

SCALES = (100, 125, 150, 200, 400)

# (기본 이름, 기준 폭, 기준 높이, 아이콘이 차지하는 비율)
# 타일은 여백을 두고, 앱 목록/스토어 아이콘은 거의 꽉 채운다.
LOGOS = (
    ("Square44x44Logo", 44, 44, 0.90),
    ("Square71x71Logo", 71, 71, 0.66),
    ("Square150x150Logo", 150, 150, 0.66),
    ("Square310x310Logo", 310, 310, 0.66),
    ("Wide310x150Logo", 310, 150, 0.66),
    ("StoreLogo", 50, 50, 0.90),
)

# 앱 목록·작업표시줄이 쓰는 고정 크기들. altform-unplated 는 Windows 가 뒤에
# 회색 판을 깔지 않는 변형이라, 배경이 있는 우리 아이콘에는 이쪽이 깔끔하다.
TARGET_SIZES = (16, 24, 32, 48, 256)


def _compose(width: int, height: int, ratio: float) -> QPixmap:
    """투명 캔버스 가운데에 아이콘을 비율만큼 그린다."""
    canvas = QPixmap(width, height)
    canvas.fill(Qt.GlobalColor.transparent)

    icon_size = max(1, int(round(min(width, height) * ratio)))
    icon = draw_icon_pixmap(icon_size)

    p = QPainter(canvas)
    p.drawPixmap((width - icon_size) // 2, (height - icon_size) // 2, icon)
    p.end()
    return canvas


def _save(pixmap: QPixmap, path: str) -> int:
    if not pixmap.save(path, "PNG"):
        raise RuntimeError("PNG 저장 실패: %s" % path)
    return os.path.getsize(path)


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication(sys.argv)
    os.makedirs(OUT_DIR, exist_ok=True)

    count = total = 0
    try:
        for name, base_w, base_h, ratio in LOGOS:
            for scale in SCALES:
                w = int(round(base_w * scale / 100))
                h = int(round(base_h * scale / 100))
                path = os.path.join(OUT_DIR, "%s.scale-%d.png" % (name, scale))
                total += _save(_compose(w, h, ratio), path)
                count += 1
            # 스케일 없는 기본 파일도 둔다 — 매니페스트가 이 이름을 가리킨다.
            path = os.path.join(OUT_DIR, "%s.png" % name)
            total += _save(_compose(base_w, base_h, ratio), path)
            count += 1

        for size in TARGET_SIZES:
            for suffix in ("", ".altform-unplated"):
                path = os.path.join(
                    OUT_DIR, "Square44x44Logo.targetsize-%d%s.png" % (size, suffix)
                )
                total += _save(_compose(size, size, 0.90), path)
                count += 1
    except (OSError, RuntimeError) as exc:
        print("assets: FAILED %s" % exc)
        return 1
    finally:
        del app

    print("assets: %s (%d files, %.1f KB)" % (OUT_DIR, count, total / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
