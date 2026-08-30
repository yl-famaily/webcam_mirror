"""build.bat 이 exe 아이콘으로 쓸 assets/icon.ico 를 만든다.

**ICO 컨테이너를 직접 쓴다.** Qt 의 이미지 라이터는 ICO 를 **읽기만** 지원해서
`pixmap.save(path, "ICO")` 는 프레임 하나짜리 파일을 만든다. 예전에는 256px 한
장만 담겨서, Windows 가 작업표시줄·Alt+Tab 용 16px 를 그 한 장에서 다운샘플했다
— `tray.py` 가 사이즈별로 정성껏 그린 그림이 통째로 버려지고 뭉갠 결과만 보였다.

Vista 이후의 ICO 는 프레임 페이로드로 PNG 를 허용하므로, 각 사이즈를 PNG 로
인코딩해 헤더만 직접 조립하면 된다. Pillow 같은 새 의존성이 필요 없다.
"""

from __future__ import annotations

import os
import struct
import sys

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QGuiApplication

from tray import ICON_SIZES, draw_icon_pixmap

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.ico")


def _png_bytes(size: int) -> bytes:
    # QBuffer 에 임시 QByteArray 를 넘기면 그 객체가 먼저 수거돼 죽는다.
    # 인자 없이 만들면 내부 버퍼를 쓰므로 수명 문제가 없다.
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not draw_icon_pixmap(size).save(buffer, "PNG"):
        raise RuntimeError("PNG 인코딩 실패: %dpx" % size)
    buffer.close()
    return bytes(buffer.data())


def write_ico(path: str, sizes: tuple[int, ...]) -> int:
    frames = [(size, _png_bytes(size)) for size in sizes]

    # ICONDIR: reserved=0, type=1(icon), count
    header = struct.pack("<HHH", 0, 1, len(frames))
    # 디렉터리 엔트리는 16바이트 고정이라 첫 페이로드 위치를 미리 계산할 수 있다
    offset = len(header) + 16 * len(frames)

    entries, payloads = [], []
    for size, data in frames:
        entries.append(
            struct.pack(
                "<BBBBHHII",
                0 if size >= 256 else size,  # 256 은 0 으로 표기하는 게 규격이다
                0 if size >= 256 else size,
                0,  # 팔레트 색 수 (트루컬러는 0)
                0,  # reserved
                1,  # color planes
                32,  # bits per pixel
                len(data),
                offset,
            )
        )
        payloads.append(data)
        offset += len(data)

    with open(path, "wb") as f:
        f.write(header)
        for entry in entries:
            f.write(entry)
        for data in payloads:
            f.write(data)
    return sum(len(d) for _, d in frames) + len(header) + 16 * len(frames)


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication(sys.argv)  # QPixmap 을 쓰려면 필요하다
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    try:
        total = write_ico(OUT, ICON_SIZES)
    except (OSError, RuntimeError) as exc:
        print("icon: FAILED %s" % exc)
        return 1
    finally:
        del app
    print("icon: %s (%d frames, %.1f KB)" % (OUT, len(ICON_SIZES), total / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
