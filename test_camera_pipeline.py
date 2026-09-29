"""Deterministic camera pipeline tests; never opens a physical webcam."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import time
import unittest
from unittest.mock import patch
import numpy as np
from PySide6.QtCore import QCoreApplication
import camera

app = QCoreApplication.instance() or QCoreApplication([])

class FakeCapture:
    instances = []
    def __init__(self, *args):
        self.count = 0
        self.released = False
        self.properties = {}
        self.instances.append(self)
    def isOpened(self): return True
    def set(self, prop, value):
        self.properties[prop] = value
        return True
    def get(self, prop): return 30
    def getBackendName(self): return "fake"
    def read(self):
        time.sleep(.01)
        self.count += 1
        return True, np.full((8, 8, 3), self.count % 256, dtype=np.uint8)
    def release(self): self.released = True

class SlowProcessor:
    def __init__(self): self.frames = []
    def process(self, frame):
        self.frames.append(int(frame[0, 0, 0]))
        time.sleep(.045)
        return frame

class FallbackCapture(FakeCapture):
    def __init__(self, _index, backend):
        super().__init__(_index, backend)
        self.backend = backend
    def getBackendName(self):
        return "MSMF" if self.backend == camera.CAP_MSMF else "DSHOW"
    def read(self):
        if self.backend == camera.CAP_DSHOW:
            return False, None
        return super().read()

class DshowOnlyCapture(FakeCapture):
    """MSMF로는 열리지 않고 DirectShow로만 열리는 장치(가상 카메라 등)."""
    def __init__(self, _index, backend):
        super().__init__(_index, backend)
        self.backend = backend
    def isOpened(self): return self.backend == camera.CAP_DSHOW
    def getBackendName(self):
        return "MSMF" if self.backend == camera.CAP_MSMF else "DSHOW"


class PipelineTests(unittest.TestCase):
    @patch("camera.cv2.VideoCapture", FakeCapture)
    def test_auto_mode_does_not_force_camera_properties(self):
        worker = camera.CameraThread(auto_mode=True)
        worker.start()
        try:
            deadline = time.monotonic() + 2
            while "frame_width" not in worker.actual_mode and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(worker.actual_mode["frame_width"], 8)
            self.assertEqual(FakeCapture.instances[-1].properties, {})
        finally:
            worker.stop()

    def test_device_modes_are_deduplicated_and_prefer_mjpg(self):
        class FakeInput:
            def get_formats(self):
                return [
                    {"index":1,"media_type_str":"H264","width":1920,"height":1080,"min_framerate":30},
                    {"index":2,"media_type_str":"MJPG","width":1280,"height":720,"min_framerate":60},
                    {"index":3,"media_type_str":"MJPG","width":1920,"height":1080,"min_framerate":30},
                ]
        class FakeGraph:
            def add_video_input_device(self, _index): pass
            def get_input_device(self): return FakeInput()
        with patch("pygrabber.dshow_graph.FilterGraph", FakeGraph):
            modes = camera.list_camera_modes(0)
        self.assertEqual(modes, [
            {"width":1280,"height":720,"fps":60,"fourcc":"MJPG","format_index":2},
            {"width":1920,"height":1080,"fps":30,"fourcc":"MJPG","format_index":3},
        ])

    @patch("camera.cv2.VideoCapture", FallbackCapture)
    @patch.object(camera, "MAX_CONSECUTIVE_FAILURES", 2)
    def test_falls_back_when_backend_opens_without_frames(self):
        worker = camera.CameraThread()
        worker.start()
        try:
            deadline = time.monotonic()+2
            while (worker.actual_mode.get("backend") != "MSMF"
                   or "frame_width" not in worker.actual_mode) and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertEqual(worker.actual_mode["backend"], "MSMF")
            self.assertEqual(worker.actual_mode["frame_width"], 8)
        finally:
            worker.stop()

    @patch("camera.cv2.VideoCapture", FakeCapture)
    def test_quality_requested_and_actual_frame_reported(self):
        worker = camera.CameraThread(quality="1080p", fps=60)
        worker.start()
        try:
            deadline = time.monotonic()+2
            while "frame_width" not in worker.actual_mode and time.monotonic()<deadline:
                time.sleep(.01)
            capture = FakeCapture.instances[-1]
            self.assertEqual(capture.properties[camera.cv2.CAP_PROP_FRAME_WIDTH],1920)
            self.assertEqual(capture.properties[camera.cv2.CAP_PROP_FRAME_HEIGHT],1080)
            self.assertEqual(capture.properties[camera.cv2.CAP_PROP_FPS],60)
            self.assertEqual(worker._timer.interval(),17)
            # The fake driver ignores the request. Report the real frame,
            # not the requested resolution or the driver's metadata.
            self.assertEqual(worker.actual_mode["frame_width"],8)
            self.assertEqual(worker.actual_mode["frame_height"],8)
            deadline = time.monotonic()+2
            while "measured_fps" not in worker.actual_mode and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertGreater(worker.actual_mode["measured_fps"],50)
        finally:
            worker.stop()

    def test_latest_and_close(self):
        box = camera.LatestFrame()
        for i in range(100): box.put(i)
        self.assertEqual(box.take(), 99)
        self.assertEqual(box.replaced, 99)
        box.close()
        box.put(100)
        self.assertIsNone(box.take(wait=True))

        buffered = camera.LatestFrame(3)
        for i in range(5): buffered.put(i)
        self.assertEqual([buffered.take(), buffered.take(), buffered.take()], [2, 3, 4])
        self.assertEqual(buffered.replaced, 2)

    @patch("camera.cv2.VideoCapture", FakeCapture)
    def test_slow_processing_and_gui_stall(self):
        processor = SlowProcessor()
        worker = camera.CameraThread(processor=processor)
        delivered = []
        worker.frame_ready.connect(lambda image: delivered.append(image.copy()))
        worker.start()
        try:
            # Deliberately do not pump GUI events: no image event backlog.
            time.sleep(.4)
            self.assertEqual(delivered, [])
            worker._deliver()
            self.assertEqual(len(delivered), 1)
            worker._deliver()
            self.assertEqual(len(delivered), 2)
            self.assertGreater(worker._raw.replaced, 5)
            self.assertGreater(worker._images.replaced, 2)
            self.assertTrue(any(b-a > 1 for a,b in zip(processor.frames, processor.frames[1:])))
            metrics = worker.diagnostics()
            self.assertLess(metrics["capture_interval_ms"]["mean"], 30)
            self.assertGreater(metrics["process_ms"]["mean"], 40)
        finally:
            worker.stop()
        self.assertFalse(worker.isRunning())
        self.assertTrue(FakeCapture.instances[-1].released)
        self.assertIsNone(worker._images.take())

    @patch("camera.cv2.VideoCapture", FakeCapture)
    def test_reconnect_and_immediate_stop(self):
        for _ in range(3):
            worker = camera.CameraThread()
            worker.start()
            worker.stop()
            self.assertFalse(worker.isRunning())
            self.assertTrue(FakeCapture.instances[-1].released)

    @patch("camera.cv2.VideoCapture", DshowOnlyCapture)
    def test_falls_back_to_dshow_when_msmf_cannot_open(self):
        """MSMF가 못 여는 장치도 DirectShow로 살아나야 한다."""
        worker = camera.CameraThread()
        worker.start()
        try:
            deadline = time.monotonic() + 2
            while "frame_width" not in worker.actual_mode and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(worker.actual_mode.get("backend"), "DSHOW")
            self.assertEqual(worker.actual_mode["frame_width"], 8)
        finally:
            worker.stop()

    def test_default_modes_cover_qualities_without_forcing_a_pixel_format(self):
        modes = camera.default_camera_modes()
        self.assertTrue(modes)
        self.assertTrue(all(m["fallback"] for m in modes))
        # 확인하지 않은 fourcc를 강제하면 검은 화면으로 멈추는 장치가 있다.
        self.assertTrue(all(m["fourcc"] == "" for m in modes))
        from settings import CAMERA_FPS_CHOICES, CAMERA_QUALITIES
        self.assertEqual(len(modes), len(CAMERA_QUALITIES) * len(CAMERA_FPS_CHOICES))
        for width, height in CAMERA_QUALITIES.values():
            self.assertIn((width, height), [(m["width"], m["height"]) for m in modes])

    def test_mode_enumeration_releases_the_device_filter(self):
        released = []
        class FakeInput:
            def get_formats(self):
                return [{"index": 0, "media_type_str": "MJPG", "width": 640,
                         "height": 480, "min_framerate": 30}]
        class FakeGraph:
            def add_video_input_device(self, _index): pass
            def get_input_device(self): return FakeInput()
            def remove_filters(self): released.append(True)
        with patch("pygrabber.dshow_graph.FilterGraph", FakeGraph):
            self.assertEqual(len(camera.list_camera_modes(0)), 1)
        self.assertEqual(released, [True])

    def test_mode_enumeration_failure_is_contained(self):
        class BoomGraph:
            def __init__(self): raise OSError("COM unavailable")
        with patch("pygrabber.dshow_graph.FilterGraph", BoomGraph):
            with self.assertLogs("camera", level="ERROR"):
                self.assertEqual(camera.list_camera_modes(0), [])
    def test_mask_is_never_stale_under_overload(self):
        """어떤 부하에서도 낡은 마스크가 화면에 나가면 안 된다.

        낡은 마스크는 **새 영상을 이전 실루엣으로 자른다** — 움직일 때 배경이
        인물처럼 남고 몸은 잘려 나간다. 그게 잔상이었다. 예산을 못 지킬 때
        물러설 곳은 마스크 신선도가 아니라 처리 해상도여야 한다.
        """
        import effects
        import settings as settings_mod

        s = settings_mod.Settings()
        s.w = s.h = 494
        s.bg_mode = settings_mod.BG_TRANSPARENT
        s.camera_fps = 60                      # 16.7ms — 일부러 빠듯하게
        proc = effects.FrameProcessor(s)

        fresh = []
        inner = effects.FrameProcessor._person_mask_inner
        with patch.object(effects.FrameProcessor, "_person_mask_inner",
                          autospec=True,
                          side_effect=lambda self, bgr: (
                              lambda r: (fresh.append(r[1]), r)[1])(inner(self, bgr))):
            for i in range(40):
                frame = np.full((480, 640, 3), 40, np.uint8)
                frame[100:300, 50 + i * 8:250 + i * 8] = (210, 190, 175)
                proc.process(frame)

        self.assertTrue(fresh, "추론 경로를 한 번도 안 탔다")
        self.assertTrue(all(fresh),
                        "낡은 마스크가 %d/%d 프레임에서 재사용됐다"
                        % (fresh.count(False), len(fresh)))
        self.assertEqual(proc._seg_every, 1)

    def test_work_resolution_ladder_has_no_dead_rungs(self):
        """해상도를 한 단계 낮추면 실제로 낮아져야 한다.

        배율을 상한으로 자르기 전에 곱하면, 고배율 화면처럼 목표가 상한보다
        큰 경우 위쪽 몇 단계가 같은 값으로 잘린다. 그동안 예산을 넘긴 채
        버티다 한 번에 뚝 떨어져 화면이 튄다.
        """
        import effects
        import settings as settings_mod

        for view in ((494, 494), (1235, 1235)):     # 100% 화면, 250% 화면
            s = settings_mod.Settings()
            s.w = s.h = 494
            s.view_size = view
            proc = effects.FrameProcessor(s)
            widths = []
            for step in range(6):
                proc._work_scale = round(1.0 - step * 0.1, 1)
                widths.append(proc._work_width(1920, 1440))
            self.assertEqual(len(set(widths)), len(widths),
                             ("사다리에 죽은 단계가 있다", view, widths))
            self.assertEqual(widths, sorted(widths, reverse=True), (view, widths))
            self.assertGreaterEqual(widths[0], max(view),
                                    ("칠해지는 픽셀보다 낮게 계산한다", view, widths))

if __name__ == "__main__": unittest.main()
