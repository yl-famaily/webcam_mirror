"""Camera capture and processing with bounded latest-frame delivery."""
from __future__ import annotations

import sys
import threading
import time
import logging
from collections import deque

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal, QTimer, Qt
from PySide6.QtGui import QImage
from settings import CAMERA_FPS_CHOICES, CAMERA_QUALITIES

REQ_WIDTH = 1280
REQ_HEIGHT = 720
DEFAULT_FPS = 60
MAX_CONSECUTIVE_FAILURES = 15
CAP_DSHOW = getattr(cv2, "CAP_DSHOW", 700)
CAP_MSMF = getattr(cv2, "CAP_MSMF", 1400)
# 백엔드마다 열 수 있는 장치가 다르다. 이 PC에서 재어 보면 내장 카메라는 둘 다
# 되지만 가상 카메라(Insta360 Virtual Camera)는 MSMF가 "can't be used to capture
# by index"로 아예 열리지 않고 DirectShow로만 열린다. 반대로 MSMF로만 열리는
# 장치도 있어서, 하나만 쓰면 그런 장치는 통째로 못 쓴다. MSMF를 먼저 두는 것은
# 지연이 더 낮고 대부분의 물리 카메라에서 잘 되기 때문이다.
CAPTURE_BACKENDS = (CAP_MSMF, CAP_DSHOW)
FALLBACK_SCAN_LIMIT = 10  # COM 열거 실패 시 직접 열어 볼 인덱스 수


class LatestFrame:
    """A small bounded mailbox; overflow discards the oldest frame."""
    def __init__(self, capacity=1):
        self.condition = threading.Condition()
        self.values = deque(maxlen=max(1, int(capacity)))
        self.closed = False
        self.replaced = 0

    def put(self, value):
        with self.condition:
            if self.closed:
                return
            if len(self.values) == self.values.maxlen:
                self.replaced += 1
            self.values.append(value)
            self.condition.notify()

    def take(self, wait=False):
        with self.condition:
            if wait:
                self.condition.wait_for(lambda: self.values or self.closed)
            return self.values.popleft() if self.values else None

    def close(self):
        with self.condition:
            self.closed = True
            self.values.clear()
            self.condition.notify_all()


class CameraThread(QThread):
    frame_ready = Signal(QImage)
    error = Signal(str)

    def __init__(self, index=0, processor=None, parent=None, quality="720p",
                 fps=DEFAULT_FPS, width=None, height=None, fourcc="", auto_mode=False):
        super().__init__(parent)
        self._index = int(index)
        self.auto_mode = auto_mode
        fallback_size = CAMERA_QUALITIES.get(quality, CAMERA_QUALITIES["720p"])
        self.requested_size = (int(width), int(height)) if width and height else fallback_size
        self.requested_fps = max(1, min(240, int(fps)))
        self.requested_fourcc = str(fourcc or "")[:4]
        self.output_size = None
        self._processor = processor
        self._stop = threading.Event()
        # MSMF may return several buffered frames back-to-back after a longer
        # read. Three frames smooth that burst without allowing latency to grow.
        self._raw = LatestFrame(3)
        # Two ready images keep MSMF bursts smooth while avoiding the ~60 ms
        # average lag measured with a three-image display buffer (~38 ms here).
        self._images = LatestFrame(2)
        self._metrics_lock = threading.Lock()
        self._samples = {key: deque(maxlen=600) for key in
                         ("capture_interval_ms", "read_ms", "process_ms", "convert_ms",
                          "delivery_interval_ms", "capture_to_delivery_ms")}
        self.actual_mode = {}
        self._last_delivery = None
        self._delivery_intervals = deque(maxlen=self.requested_fps)
        self._last_capture = None
        self._capture_intervals = deque(maxlen=max(12, self.requested_fps))
        # This QObject lives on the GUI thread. Polling avoids one queued Qt
        # event per image when painting or a menu temporarily blocks the GUI.
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(max(1, round(1000 / self.requested_fps)))
        self._timer.timeout.connect(self._deliver)

    @property
    def index(self):
        return self._index

    def start(self):
        self._timer.start()
        super().start()

    def _record(self, key, value):
        with self._metrics_lock:
            self._samples[key].append(value)

    def diagnostics(self):
        """Bounded timing samples; no image data or disk writes."""
        with self._metrics_lock:
            result = {}
            for key, values in self._samples.items():
                if values:
                    ordered = sorted(values)
                    result[key] = {"mean": sum(values) / len(values),
                                   "p95": ordered[min(len(ordered)-1, int(len(ordered)*.95))],
                                   "max": max(values), "count": len(values)}
        result.update(actual_mode=dict(self.actual_mode),
                      capture_replaced=self._raw.replaced,
                      display_replaced=self._images.replaced)
        return result

    def _deliver(self):
        item = self._images.take()
        if item is None or self._stop.is_set():
            return
        image, captured = item
        now = time.perf_counter()
        if self._last_delivery is not None:
            interval = (now-self._last_delivery)*1000
            self._record("delivery_interval_ms", interval)
            self._delivery_intervals.append(interval)
            if len(self._delivery_intervals) >= 12:
                mean_ms = sum(self._delivery_intervals) / len(self._delivery_intervals)
                self.actual_mode = dict(self.actual_mode,
                                        display_fps=1000.0 / mean_ms)
        self._last_delivery = now
        self._record("capture_to_delivery_ms", (now-captured)*1000)
        self.frame_ready.emit(image)

    def _accept_frame(self, frame):
        if frame is None or self._stop.is_set():
            return
        captured = time.perf_counter()
        self.actual_mode = dict(self.actual_mode, frame_width=frame.shape[1],
                                frame_height=frame.shape[0])
        if self._last_capture is not None:
            interval = (captured - self._last_capture) * 1000
            self._record("capture_interval_ms", interval)
            self._capture_intervals.append(interval)
            if len(self._capture_intervals) >= 12:
                mean_ms = sum(self._capture_intervals) / len(self._capture_intervals)
                self.actual_mode = dict(self.actual_mode,
                                        measured_fps=1000.0 / mean_ms)
        self._last_capture = captured
        self._raw.put((frame, captured))

    def _capture(self):
        try:
            last_error = "카메라를 열 수 없습니다. 다른 앱의 카메라 사용 여부를 확인하세요."
            for backend in CAPTURE_BACKENDS:
                if self._stop.is_set():
                    return
                cap = cv2.VideoCapture(self._index, backend)
                try:
                    if not cap.isOpened():
                        continue
                    if not self.auto_mode:
                        if len(self.requested_fourcc) == 4:
                            cap.set(cv2.CAP_PROP_FOURCC,
                                    cv2.VideoWriter_fourcc(*self.requested_fourcc))
                        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.requested_size[0])
                        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.requested_size[1])
                        cap.set(cv2.CAP_PROP_FPS, self.requested_fps)
                    self.actual_mode = {"width": cap.get(cv2.CAP_PROP_FRAME_WIDTH),
                                        "height": cap.get(cv2.CAP_PROP_FRAME_HEIGHT),
                                        "fps": cap.get(cv2.CAP_PROP_FPS),
                                        "fourcc": cap.get(cv2.CAP_PROP_FOURCC),
                                        "backend": cap.getBackendName()}
                    failures = 0
                    while not self._stop.is_set():
                        began = time.perf_counter()
                        ok, frame = cap.read()
                        captured = time.perf_counter()
                        self._record("read_ms", (captured-began)*1000)
                        if not ok or frame is None:
                            failures += 1
                            if failures >= MAX_CONSECUTIVE_FAILURES:
                                # 열리기는 했는데 프레임이 없는 백엔드가 있다.
                                # 다음 백엔드로 넘어가야 하므로 여기서 끝내지 않는다.
                                last_error = ("%s에서 영상이 오지 않습니다."
                                              % cap.getBackendName())
                                break
                            self._stop.wait(.05)
                            continue
                        failures = 0
                        self._accept_frame(frame)
                    if self._stop.is_set():
                        return
                finally:
                    cap.release()
            raise RuntimeError(last_error)
        except Exception as exc:
            if not self._stop.is_set():
                self.error.emit("카메라 오류: %s" % exc)
        finally:
            self._stop.set()
            self._raw.close()
            self._images.close()

    def run(self):
        reader = threading.Thread(target=self._capture, name="WebcamCapture")
        reader.start()
        try:
            while not self._stop.is_set():
                item = self._raw.take(wait=True)
                if item is None:
                    break
                frame, captured = item
                began = time.perf_counter()
                if self._processor is not None:
                    frame = self._processor.process(frame)
                converted = time.perf_counter()
                self._record("process_ms", (converted-began)*1000)
                if frame.ndim == 3 and frame.shape[2] == 4:
                    buf = cv2.cvtColor(frame, cv2.COLOR_BGRA2RGBA)
                    fmt = QImage.Format.Format_RGBA8888
                else:
                    buf = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    fmt = QImage.Format.Format_RGB888
                buf = np.ascontiguousarray(buf)
                h, w = buf.shape[:2]
                self.output_size = (w, h)
                image = QImage(buf.data, w, h, buf.strides[0], fmt).copy()
                self._record("convert_ms", (time.perf_counter()-converted)*1000)
                self._images.put((image, captured))
        except Exception as exc:
            if not self._stop.is_set():
                self.error.emit("영상 처리 오류: %s" % exc)
        finally:
            self._stop.set()
            self._raw.close()
            self._images.close()
            # Never terminate a thread inside OpenCV/ONNX native code.
            reader.join()

    def stop(self):
        self._timer.stop()
        self._stop.set()
        self._raw.close()
        self._images.close()
        self.wait()


def _relax_comtypes_typelib_check() -> None:
    """빌드된 exe에서 comtypes 타입 라이브러리 mtime 검사를 끈다.

    comtypes.gen 모듈은 빌드한 PC의 quartz.dll 수정 시각을 기억하고, import 때
    지금 파일과 1초라도 다르면 ImportError("Typelib different than module")를
    낸다. Windows 업데이트로 quartz.dll이 바뀌면 exe 안의 모듈을 다시 만들 수
    없으니 pygrabber import가 매번 실패한다. 그러면 장치 이름이 "카메라 N"으로
    나오고, 모든 장치를 GUI 스레드에서 직접 열어 보는 탐색으로 떨어진다.
    PyInstaller는 sys.frozen으로 이 검사를 건너뛰지만 Nuitka는 sys.frozen을
    두지 않으므로 여기서 같은 동작을 맞춘다. 인터페이스 정의(GUID)는 OS
    버전이 바뀌어도 그대로라 생성 코드 버전만 확인하면 충분하다.
    """
    if not ("__compiled__" in globals() or getattr(sys, "frozen", False)):
        return

    # 이것이 실제로 듣는 우회다. comtypes 의 _check_version 은 함수 안에서
    # `if not hasattr(sys, "frozen")` 일 때만 mtime 을 본다. 속성 하나만 두면
    # 원본 함수가 스스로 검사를 건너뛴다.
    #
    # 아래 몽키패치만으로는 부족했다 — Nuitka 로 컴파일된 comtypes.gen 모듈이
    # 원본 _check_version 참조를 이미 붙들고 있어서 모듈 속성을 바꿔도 닿지
    # 않는다. 0.1.10 의 exe 가 정확히 그래서 실패했고, 장치 이름이 "카메라 N"
    # 으로 떨어졌다. 그래도 패치는 남겨 둔다 — 두 경로 다 막아 둘 이유가 있다.
    if not hasattr(sys, "frozen"):
        sys.frozen = True

    try:
        import comtypes
        from comtypes import _tlib_version_checker
    except Exception:
        return

    def check_generator_version(actual, tlib_cached_mtime=None):
        from comtypes.tools.codegenerator import version as required
        if actual != required:
            raise ImportError("Wrong version")

    # 생성 모듈은 `from comtypes import _check_version` 으로 가져간다.
    comtypes._check_version = check_generator_version
    _tlib_version_checker._check_version = check_generator_version


def list_cameras(active_index: int | None = None) -> list[tuple[int, str]]:
    """(인덱스, 표시이름) 목록. 메뉴를 열 때만 호출한다."""
    try:
        _relax_comtypes_typelib_check()
        from pygrabber.dshow_graph import FilterGraph

        names = FilterGraph().get_input_devices()
        if names:
            return [(i, name or "카메라 %d" % i) for i, name in enumerate(names)]
    except Exception:
        pass  # pygrabber 미설치/COM 실패 → 인덱스 탐색으로 대체

    # COM 열거가 실패했을 때만 오는 길이다. 범위를 5 로 두면 가상 카메라를
    # 여러 개 깐 PC(OBS·PRISM·Insta360 …)에서 뒤쪽 장치가 통째로 안 보인다.
    # 실제로 이 PC 는 장치가 6 개고 Insta360 이 5 번이라 목록에서 사라졌다.
    # 한 칸마다 장치를 직접 열어 보므로 공짜가 아니다 — 그래서 무한정 늘리지
    # 않고 흔한 구성을 덮는 선에서 멈춘다.
    found: list[tuple[int, str]] = []
    for i in range(FALLBACK_SCAN_LIMIT):
        if i == active_index:
            # 현재 사용 중인 장치는 다시 열 수 없으므로 탐색하지 않고 추가
            found.append((i, "카메라 %d" % i))
            continue
        cap = cv2.VideoCapture(i, CAP_DSHOW)
        if cap.isOpened():
            found.append((i, "카메라 %d" % i))
        cap.release()
    return found


def list_camera_modes(index: int) -> list[dict]:
    """DirectShow가 광고한 실제 출력 모드 목록을 반환한다.

    같은 해상도/FPS가 여러 압축 형식으로 중복되면 대역폭이 작은 MJPG를 우선한다.
    장치를 직접 열어 조합을 추측하지 않으므로 지원하지 않는 모드로 멈추지 않는다.
    """
    graph = None
    try:
        _relax_comtypes_typelib_check()
        from pygrabber.dshow_graph import FilterGraph

        graph = FilterGraph()
        graph.add_video_input_device(int(index))
        formats = graph.get_input_device().get_formats()
    except Exception:
        logging.getLogger(__name__).exception("Camera mode enumeration failed: index=%s", index)
        return []
    finally:
        # add_video_input_device 는 장치 필터를 그래프에 붙여 둔다. 떼지 않으면
        # 그래프가 GC 될 때까지 장치를 잡고 있어서, 바로 뒤에 오는 캡처 열기가
        # 실패할 수 있다. 파이썬 GC 시점에 기대지 않고 여기서 확실히 놓는다.
        if graph is not None:
            try:
                graph.remove_filters()
            except Exception:
                logging.getLogger(__name__).debug("remove_filters failed", exc_info=True)

    preferred = {"MJPG": 0, "NV12": 1, "YUY2": 2, "UYVY": 3, "H264": 4}
    unique = {}
    for item in formats:
        width = abs(int(item.get("width", 0)))
        height = abs(int(item.get("height", 0)))
        fps = int(round(float(item.get("min_framerate", 0))))
        fourcc = str(item.get("media_type_str") or "")[:4]
        if width <= 0 or height <= 0 or fps <= 0:
            continue
        mode = {"width": width, "height": height, "fps": fps,
                "fourcc": fourcc, "format_index": int(item["index"])}
        key = (width, height, fps)
        old = unique.get(key)
        if old is None or preferred.get(fourcc, 99) < preferred.get(old["fourcc"], 99):
            unique[key] = mode
    return sorted(unique.values(),
                  key=lambda m: (m["width"] * m["height"], m["fps"]))


def default_camera_modes() -> list[dict]:
    """장치가 모드 목록을 알려 주지 않을 때 대신 보여 줄 표준 조합.

    실제 지원 여부를 확인한 목록이 아니므로 픽셀 형식(fourcc)은 비워 둔다.
    해상도/FPS 요청은 장치가 못 맞추면 OpenCV가 가장 가까운 모드로 대신 열어
    주지만, 지원하지 않는 fourcc를 강제하면 검은 화면으로 멈추는 장치가 있다.
    """
    return sorted(
        ({"width": width, "height": height, "fps": fps,
          "fourcc": "", "format_index": -1, "fallback": True}
         for width, height in CAMERA_QUALITIES.values()
         for fps in CAMERA_FPS_CHOICES),
        key=lambda mode: (mode["width"] * mode["height"], mode["fps"]))
