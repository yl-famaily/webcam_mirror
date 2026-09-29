"""Quality selection tests without opening a camera or changing user settings."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from PySide6.QtWidgets import QApplication
import settings
import effects
import main
import menu
import panel
import theme

app = QApplication.instance() or QApplication([])
theme.apply_theme(app)


class QualityTests(unittest.TestCase):
    def test_save_load_and_invalid(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(settings, "APP_DIR", root), patch.object(settings, "SETTINGS_PATH", str(Path(root)/"settings.json")):
                s = settings.Settings(camera_quality="1080p", camera_width=1920,
                                      camera_height=1080, camera_fps=25,
                                      camera_fourcc="MJPG")
                s.save()
                self.assertEqual(settings.Settings.load().camera_quality, "1080p")
                loaded = settings.Settings.load()
                self.assertEqual((loaded.camera_width, loaded.camera_height,
                                  loaded.camera_fps, loaded.camera_fourcc),
                                 (1920, 1080, 25, "MJPG"))
                s.camera_quality = "invalid"
                s.sanitize()
                self.assertEqual(s.camera_quality, "720p")
                s.camera_fps = 999
                s.sanitize()
                self.assertEqual(s.camera_fps, 240)

    def test_effect_detail_and_size(self):
        for quality, (width, height) in settings.CAMERA_QUALITIES.items():
            s = settings.Settings(camera_quality=quality, camera_width=width,
                                  camera_height=height,
                                  bg_mode=settings.BG_TRANSPARENT)
            processor = effects.FrameProcessor(s)
            # A high frequency pattern catches accidental shrinking/upscaling.
            row = ((np.arange(width) % 2)*255).astype(np.uint8)
            frame = np.broadcast_to(row[None, :, None], (height, width, 3)).copy()
            # 처리 해상도는 캡처가 아니라 창이 정한다. 창을 채우기에 모자라서도
            # 안 되고, 원본보다 커져서도 안 된다.
            work = processor._downscale(frame, processor._work_width(width, height))
            wh, ww = work.shape[:2]
            self.assertLessEqual(ww, width)
            self.assertGreaterEqual(ww, min(width, s.w))
            with patch.object(processor, "_person_mask",
                              return_value=np.ones((wh, ww), np.float32)):
                result = processor.process(frame)
            self.assertEqual(result.shape, (wh, ww, 4))
            self.assertTrue(np.array_equal(result[:, :, :3], work))
        s.camera_quality = "2160p"
        small = np.zeros((480,640,3), np.uint8)
        self.assertEqual(effects.FrameProcessor(s)._downscale(small,3840).shape,small.shape)

    def test_controls_and_actual_status(self):
        with patch.object(settings.Settings, "load", return_value=settings.Settings()), patch.object(main, "apply_default_placement"), patch.object(main.QSystemTrayIcon, "isSystemTrayAvailable", return_value=False):
            ctl = main.AppController(app)
        widget = None
        try:
            with patch.object(ctl, "stop_camera"), patch.object(ctl, "_notify"), \
                    patch.object(main, "list_camera_modes", return_value=[]), \
                    patch.object(main, "CameraThread") as worker:
                ctl._start_camera(0, "Camera", save_selection=True)
                # 장치가 모드를 안 알려 줘도 목록이 비지 않아야 한다. 비면
                # 사용자가 해상도를 아예 못 고른다.
                self.assertTrue(ctl.camera_modes_are_default())
                self.assertTrue(ctl.camera_modes())
                self.assertTrue(all(m["fallback"] for m in ctl.camera_modes()))
                # 확인 못 한 픽셀 형식은 강제하지 않는다.
                self.assertEqual(worker.call_args.kwargs["fourcc"], "")
                self.assertEqual(ctl.settings.camera_fourcc, "")
                worker.return_value.start.assert_called_once()
                self.assertFalse(ctl._camera_retry_timer.isActive())
            with patch.object(ctl, "stop_camera"), patch.object(ctl, "_notify"),                     patch.object(main, "list_camera_modes",
                                 return_value=[{"width": 1280, "height": 720,
                                                "fps": 30, "fourcc": "MJPG"}]),                     patch.object(main, "CameraThread"):
                ctl._start_camera(0, "Camera", save_selection=True)
                self.assertFalse(ctl.camera_modes_are_default())
            ctl.camera = None
            ctl.settings.camera_index = 0
            ctl.settings.camera_name = "Sony Camera"
            with patch.object(ctl, "cameras", return_value=[(0, "내장"), (2, "Sony Camera")]):
                self.assertEqual(ctl._resolved_camera(), (2, "Sony Camera"))
            with patch.object(ctl, "restart_camera") as restart:
                ctl._camera_modes = [
                    {"width":1280,"height":720,"fps":30,"fourcc":"MJPG"},
                    {"width":1920,"height":1080,"fps":30,"fourcc":"MJPG"},
                ]
                ctl.settings.camera_width = 1280
                ctl.settings.camera_height = 720
                ctl.settings.camera_fps = 30
                ctl.settings.camera_fourcc = "MJPG"
                widget = panel.SettingsPanel(ctl, ctl.window)
                full_hd = (1920,1080,30,"MJPG")
                widget.camera_mode.setCurrentIndex(
                    widget.camera_mode.findData("1920x1080|30|MJPG"))
                self.assertEqual((ctl.settings.camera_width, ctl.settings.camera_height,
                                  ctl.settings.camera_fps, ctl.settings.camera_fourcc), full_hd)
                restart.assert_called_once()
                with patch.object(ctl, "cameras", return_value=[]):
                    popup = menu.build_menu(ctl)
                mode_menu = next(a.menu() for a in popup.actions()
                                 if a.text()=="카메라 출력 모드")
                mode_menu.actions()[0].trigger()
                self.assertEqual((ctl.settings.camera_width, ctl.settings.camera_height,
                                  ctl.settings.camera_fps, ctl.settings.camera_fourcc),
                                 (1280,720,30,"MJPG"))
                self.assertEqual(restart.call_count,2)
                ctl.camera = SimpleNamespace(actual_mode={"frame_width":1280,"frame_height":720,"measured_fps":29.7},output_size=(640,360))
                status = ctl.camera_quality_status()
                self.assertIn("1280×720", status)
                self.assertIn("29.7 FPS", status)
                self.assertIn("640×360", status)
        finally:
            ctl.camera = None
            ctl._camera_retry_timer.stop()
            ctl._save_timer.stop()
            ctl.hotkeys.unregister_all()
            app.aboutToQuit.disconnect(ctl._on_about_to_quit)
            app.removeNativeEventFilter(ctl.hotkeys)
            if widget: widget.close()
            ctl.window.close()


if __name__ == "__main__": unittest.main()
