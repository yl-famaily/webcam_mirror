"""웹캠 캡처 스레드와 장치 목록 조회."""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

REQ_WIDTH = 1280
REQ_HEIGHT = 720
REQ_FPS = 30
MAX_CONSECUTIVE_FAILURES = 15

# opencv 5.x 에서도 동일하지만, 상수가 없을 경우를 대비한 기본값
CAP_DSHOW = getattr(cv2, "CAP_DSHOW", 700)


class CameraThread(QThread):
    """백그라운드에서 프레임을 읽어 QImage로 넘긴다."""

    frame_ready = Signal(QImage)
    error = Signal(str)

    def __init__(self, index: int = 0, processor=None, parent=None):
        super().__init__(parent)
        self._index = int(index)
        self._processor = processor  # effects.FrameProcessor (없으면 원본 그대로)
        self._running = False

    @property
    def index(self) -> int:
        return self._index

    def run(self) -> None:
        self._running = True

        # DirectShow 우선 — Windows 기본 MSMF 백엔드는 초기화에 수 초가 걸린다.
        cap = cv2.VideoCapture(self._index, CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(self._index)
        if not cap.isOpened():
            cap.release()
            self.error.emit(
                "카메라 %d번을 열 수 없습니다.\n다른 앱이 사용 중인지 확인하세요.\n(우클릭 → 카메라)"
                % self._index
            )
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, REQ_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, REQ_HEIGHT)
        cap.set(cv2.CAP_PROP_FPS, REQ_FPS)

        failures = 0
        try:
            while self._running:
                ok, frame = cap.read()
                if not ok or frame is None:
                    failures += 1
                    if failures >= MAX_CONSECUTIVE_FAILURES:
                        self.error.emit("카메라 연결이 끊어졌습니다.\n(우클릭 → 카메라)")
                        break
                    self.msleep(50)
                    continue

                failures = 0
                if self._processor is not None:
                    # 보정은 GUI 스레드를 막지 않도록 여기서 끝낸다.
                    frame = self._processor.process(frame)
                # 배경 투명 모드에서는 4채널(BGRA)이 넘어온다.
                if frame.ndim == 3 and frame.shape[2] == 4:
                    buf = np.ascontiguousarray(
                        cv2.cvtColor(frame, cv2.COLOR_BGRA2RGBA)
                    )
                    fmt, depth = QImage.Format.Format_RGBA8888, 4
                else:
                    buf = np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    fmt, depth = QImage.Format.Format_RGB888, 3
                h, w = buf.shape[:2]
                # numpy 버퍼는 다음 read()에서 재사용되므로 copy()가 반드시 필요하다.
                img = QImage(buf.data, w, h, w * depth, fmt).copy()
                self.frame_ready.emit(img)
        except Exception as exc:  # 캡처 중 예기치 못한 오류로 앱이 죽지 않게
            self.error.emit("카메라 오류: %s" % exc)
        finally:
            cap.release()

    def stop(self) -> None:
        self._running = False
        if not self.wait(2000):
            self.terminate()
            self.wait(500)


def list_cameras(active_index: int | None = None) -> list[tuple[int, str]]:
    """(인덱스, 표시이름) 목록. 메뉴를 열 때만 호출한다."""
    try:
        from pygrabber.dshow_graph import FilterGraph

        names = FilterGraph().get_input_devices()
        if names:
            return [(i, name or "카메라 %d" % i) for i, name in enumerate(names)]
    except Exception:
        pass  # pygrabber 미설치/COM 실패 → 인덱스 탐색으로 대체

    found: list[tuple[int, str]] = []
    for i in range(5):
        if i == active_index:
            # 현재 사용 중인 장치는 다시 열 수 없으므로 탐색하지 않고 추가
            found.append((i, "카메라 %d" % i))
            continue
        cap = cv2.VideoCapture(i, CAP_DSHOW)
        if cap.isOpened():
            found.append((i, "카메라 %d" % i))
        cap.release()
    return found
