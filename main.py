"""Webcam Mirror — 바탕화면에 띄우는 작은 웹캠 오버레이."""

from __future__ import annotations

import sys

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

import theme
import winapi
from camera import CameraThread, default_camera_modes, list_camera_modes
from effects import FrameProcessor
from menu import ask_color, ask_image
from overlay import OverlayWindow
from settings import (
    BG_IMAGE,
    SHAPE_ELLIPSE,
    SHAPE_RECT,
    Settings,
    apply_default_placement,
    fit_to_ratio,
    ratio_of,
)
from tray import TrayIcon, make_icon

APP_VERSION = "0.1.11"
# 뮤텍스/파이프 이름. 바꾸면 구버전이 돌고 있어도 못 알아보니 고정한다.
SINGLE_INSTANCE_KEY = "WebcamMirror-SingleInstance-v1"
SAVE_DEBOUNCE_MS = 600
CAMERA_CACHE_SEC = 30.0
CAMERA_RETRY_DELAYS_MS = (800, 1600, 3000)


class AppController:
    """설정 · 창 · 카메라 · 트레이 · 단축키를 묶는 중앙 객체."""

    def __init__(self, app: QApplication):
        self.app = app
        self.settings = Settings.load()
        apply_default_placement(self.settings)

        self.window = OverlayWindow(self.settings)
        self.window.controller = self

        self.processor = FrameProcessor(self.settings)
        self.camera: CameraThread | None = None
        self._click_through_warned = False
        self._context_menu = None  # 우클릭 메뉴 — 하나만 만들어 재사용한다
        self._panel = None  # 설정 패널 — 처음 열 때 만든다 (시작 시간 보호)
        self._panel_was_open = False  # 창을 숨길 때 패널이 열려 있었는지
        self._camera_cache: tuple[float, list[tuple[int, str]]] | None = None
        self._camera_modes: list[dict] = []
        self._camera_modes_are_default = False
        self._camera_retry_count = 0
        self._camera_retry_timer = QTimer(app)
        self._camera_retry_timer.setSingleShot(True)
        self._camera_retry_timer.timeout.connect(self._retry_camera)

        # 드래그/리사이즈 중 매번 디스크에 쓰지 않도록 저장을 지연시킨다.
        self._save_timer = QTimer(app)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self.settings.save)
        self.window.settings_changed.connect(self._save_timer.start)

        self.tray: TrayIcon | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = TrayIcon(self)
            self.tray.show()

        self.hotkeys = winapi.HotkeyManager()
        self._register_hotkeys()
        app.installNativeEventFilter(self.hotkeys)

        app.aboutToQuit.connect(self._on_about_to_quit)

    # ---------------------------------------------------------- 시작/종료 --

    def start(self) -> None:
        self.window.show()
        self.set_always_on_top(self.settings.always_on_top)
        if self.settings.click_through:
            self._apply_click_through()
        self.restart_camera()

    def _register_hotkeys(self) -> None:
        mods = winapi.MOD_CONTROL | winapi.MOD_ALT
        self.hotkeys.register(mods, winapi.VK_M, self.toggle_visible, "Ctrl+Alt+M")
        self.hotkeys.register(
            mods, winapi.VK_F, lambda: self.set_mirror(not self.settings.mirror), "Ctrl+Alt+F"
        )
        self.hotkeys.register(
            mods, winapi.VK_T,
            lambda: self.set_always_on_top(not self.settings.always_on_top), "Ctrl+Alt+T",
        )
        self.hotkeys.register(
            mods, winapi.VK_C,
            lambda: self.set_click_through(not self.settings.click_through), "Ctrl+Alt+C",
        )
        self.hotkeys.register(
            mods, winapi.VK_S, lambda: self.toggle_panel(activate=True), "Ctrl+Alt+S"
        )

    def _on_about_to_quit(self) -> None:
        self._camera_retry_timer.stop()
        self.stop_camera()
        self.hotkeys.unregister_all()
        self._save_timer.stop()
        self.settings.save()
        if self.tray is not None:
            self.tray.hide()

    def quit(self) -> None:
        self.app.quit()

    def context_menu(self):
        """우클릭 메뉴. 매번 새로 만들면 오버레이 자식으로 쌓이므로 재사용한다."""
        from PySide6.QtWidgets import QMenu
        from menu import rebuild_into

        if self._context_menu is None:
            self._context_menu = QMenu(self.window)
            theme.style_menu(self._context_menu)
            self._context_menu.aboutToShow.connect(
                lambda: rebuild_into(self._context_menu, self)
            )
            rebuild_into(self._context_menu, self)
        return self._context_menu

    def cameras(self, force: bool = False) -> list[tuple[int, str]]:
        """장치 목록. 메뉴를 열 때마다 COM 열거와 장치 프로빙을 하면 우클릭이
        눈에 띄게 느려지므로 30초간 캐시한다."""
        import time as _time

        from camera import list_cameras

        now = _time.monotonic()
        if not force and self._camera_cache is not None:
            stamped, devices = self._camera_cache
            if now - stamped < CAMERA_CACHE_SEC:
                return devices
        devices = list_cameras(self.settings.camera_index)
        self._camera_cache = (now, devices)
        return devices

    @property
    def version(self) -> str:
        return APP_VERSION

    # -------------------------------------------------------------- 패널 --

    def open_panel(self, activate: bool = False) -> None:
        """`activate` 는 키보드로 열었을 때만 True 다.

        패널은 `WA_ShowWithoutActivating` 으로 떠서 포커스를 안 뺏는다 —
        방송 중에 쓰는 앱이라 그게 맞다. 그런데 그대로 두면 Ctrl+Alt+S 로 열어도
        키보드 포커스가 이전 앱에 남아, Tab 을 눌러도 패널이 아니라 그 앱으로
        간다. 마우스 없이는 패널에 들어갈 수가 없다. 그래서 **키보드로 열었을
        때만** 활성화한다 — 그 시점엔 사용자가 키보드를 쓰겠다고 밝힌 것이다.
        """
        if not self.window.isVisible():
            self.set_hidden(False)  # 부모가 숨어 있으면 패널도 안 보인다
        if self._panel is None:
            from panel import SettingsPanel

            self._panel = SettingsPanel(self, self.window)
        self._panel.sync()
        self._panel.show()
        self._panel.reposition()
        if activate:
            self._panel.activateWindow()
            self._panel.focus_first_control()

    def close_panel(self) -> None:
        if self._panel is not None:
            self._panel.hide()

    def toggle_panel(self, activate: bool = False) -> None:
        if self.panel_visible:
            self.close_panel()
        else:
            self.open_panel(activate)

    def panel_was_open(self) -> bool:
        """메뉴 라벨용. 우클릭하면 바깥클릭 필터가 패널을 먼저 닫아 버려서,
        메뉴를 채우는 시점의 panel_visible 은 언제나 False 다. 그래서 '방금
        닫혔는지'를 따로 기억해 라벨을 정한다."""
        return self.panel_visible or (self._panel is not None and self._panel.just_closed())

    @property
    def panel_visible(self) -> bool:
        return self._panel is not None and self._panel.isVisible()

    def reposition_panel(self) -> None:
        if self.panel_visible:
            self._panel.reposition()

    def sync_panel(self) -> None:
        """설정이 밖에서 바뀌었을 때 패널 위젯을 맞춘다 (메뉴·단축키 경유)."""
        if self._panel is not None and self._panel.isVisible():
            self._panel.sync()

    def _notify(self) -> None:
        """설정을 바꾼 뒤 부른다 — 저장 예약 + 패널 UI 동기화.

        이걸 안 부르면 메뉴나 단축키로 값을 바꿨을 때 패널이 낡은 값을 계속
        보여준다. 패널이 스스로 부른 경우에도 안전한데, sync() 가 신호를 막고
        값만 넣기 때문이다.
        """
        self._save_timer.start()
        self.sync_panel()

    def diag_line(self) -> str:
        return "v%s · 추론 %s" % (APP_VERSION, self.backend)

    @property
    def backend(self) -> str:
        """현재 세그멘테이션 백엔드. 배경 기능을 한 번도 안 켰으면 아직 없다."""
        return self.processor.backend if self.processor.backend != "none" else "대기"


    # ------------------------------------------------------------- 카메라 --

    def stop_camera(self) -> None:
        if self.camera is None:
            return
        camera, self.camera = self.camera, None
        try:
            camera.frame_ready.disconnect(self.window.on_frame)
            camera.frame_ready.disconnect(self._on_camera_frame)
            camera.error.disconnect(self._on_camera_error)
        except (RuntimeError, TypeError):
            pass
        camera.stop()

    def restart_camera(self) -> None:
        self._camera_cache = None  # 다시 연결할 땐 목록도 새로 본다
        self._camera_retry_count = 0
        self._camera_retry_timer.stop()
        resolved = self._resolved_camera()
        if resolved is None:
            self.stop_camera()
            self.window.on_error(
                "저장된 카메라를 찾을 수 없습니다.\n우클릭 → 카메라에서 연결된 장치를 선택해 주세요."
            )
            return
        index, name = resolved
        self._start_camera(index, name, save_selection=True)

    def switch_camera(self, index: int, name: str = "") -> None:
        self._camera_retry_count = 0
        self._camera_retry_timer.stop()
        self._start_camera(index, name, save_selection=True)

    def _resolved_camera(self) -> tuple[int, str] | None:
        """저장된 이름을 우선해 장치 순서가 바뀌어도 같은 카메라를 찾는다."""
        devices = self.cameras(force=True)
        if not devices:
            return self.settings.camera_index, self.settings.camera_name
        if self.settings.camera_name:
            for index, name in devices:
                if name == self.settings.camera_name:
                    return index, name
            # 실제 장치 이름을 저장한 경우에는 같은 인덱스의 다른 장치를 열지
            # 않는다. USB 재연결로 순서가 바뀌면 엉뚱한 카메라가 선택된다.
            if ("�" not in self.settings.camera_name
                    and not self.settings.camera_name.startswith("카메라 ")):
                return None
        for index, name in devices:
            if index == self.settings.camera_index:
                return index, name
        return devices[0]

    def _start_camera(self, index: int, name: str, save_selection: bool) -> None:
        self.stop_camera()
        modes = list_camera_modes(index)
        # 장치가 모드를 안 알려 주는 경우(가상 카메라, COM 열거 실패, 드라이버가
        # 형식을 숨기는 경우)가 있다. 그때 목록을 비워 두면 사용자는 해상도를
        # 아예 못 고르고, 저장된 값이 그대로 굳는다. 표준 조합을 기본값으로
        # 대신 내보내 고를 수 있게 한다.
        self._camera_modes_are_default = not modes
        if not modes:
            modes = default_camera_modes()
        self._camera_modes = modes
        selected = self._select_camera_mode(modes)
        if selected:
            self.settings.camera_width = selected["width"]
            self.settings.camera_height = selected["height"]
            self.settings.camera_fps = selected["fps"]
            self.settings.camera_fourcc = selected["fourcc"]
        if save_selection:
            self.settings.camera_index = int(index)
            self.settings.camera_name = name
            self._notify()

        self.processor.reset()
        self.window.set_status("카메라 여는 중...")
        camera = CameraThread(index, self.processor,
                              quality=self.settings.camera_quality,
                              fps=self.settings.camera_fps,
                              width=self.settings.camera_width,
                              height=self.settings.camera_height,
                              fourcc=self.settings.camera_fourcc,
                              # 목록이 기본값 대체여도 해상도/FPS 는 요청한다.
                              # 그 값들은 못 맞추면 가장 가까운 모드로 열릴 뿐이고,
                              # 위험한 fourcc 는 기본 목록에서 비워 두었다.
                              auto_mode=False)
        camera.frame_ready.connect(self.window.on_frame)
        camera.frame_ready.connect(self._on_camera_frame)
        camera.error.connect(self._on_camera_error)
        self.camera = camera
        camera.start()

    def _on_camera_frame(self, _image) -> None:
        self._camera_retry_count = 0
        self._camera_retry_timer.stop()

    def _on_camera_error(self, message: str) -> None:
        if self._camera_retry_count >= len(CAMERA_RETRY_DELAYS_MS):
            self.window.on_error(
                message + "\n우클릭 → 카메라에서 장치를 다시 선택해 주세요."
            )
            return
        delay = CAMERA_RETRY_DELAYS_MS[self._camera_retry_count]
        self._camera_retry_count += 1
        self.window.set_status(
            "%s\n%.1f초 후 자동으로 다시 연결합니다. (%d/%d)" %
            (message, delay / 1000.0, self._camera_retry_count,
             len(CAMERA_RETRY_DELAYS_MS))
        )
        self._camera_retry_timer.start(delay)

    def _retry_camera(self) -> None:
        resolved = self._resolved_camera()
        if resolved is None:
            self._on_camera_error("저장된 카메라가 아직 연결되지 않았습니다.")
            return
        self._start_camera(resolved[0], resolved[1], save_selection=False)

    def _select_camera_mode(self, modes: list[dict]) -> dict | None:
        if not modes:
            return None
        wanted = (self.settings.camera_width, self.settings.camera_height,
                  self.settings.camera_fps, self.settings.camera_fourcc)
        for mode in modes:
            if (mode["width"], mode["height"], mode["fps"], mode["fourcc"]) == wanted:
                return mode
        # 픽셀 형식이 바뀌었어도 같은 크기/FPS면 그 장치가 선호하는 형식을 쓴다.
        for mode in modes:
            if (mode["width"], mode["height"], mode["fps"]) == wanted[:3]:
                return mode
        return min(modes, key=lambda mode: (
            abs(mode["width"] * mode["height"]
                - self.settings.camera_width * self.settings.camera_height),
            abs(mode["fps"] - self.settings.camera_fps)))

    def camera_modes(self) -> list[dict]:
        return list(self._camera_modes)

    def camera_modes_are_default(self) -> bool:
        """목록이 장치에서 읽은 것이 아니라 기본값 대체인지."""
        return self._camera_modes_are_default

    def set_camera_mode(self, mode_key) -> None:
        if isinstance(mode_key, str):
            try:
                size, fps, fourcc = mode_key.split("|")
                width, height = size.split("x")
                mode_key = (int(width), int(height), int(fps), fourcc)
            except (TypeError, ValueError):
                return
        try:
            width, height, fps, fourcc = mode_key
        except (TypeError, ValueError):
            return
        match = next((mode for mode in self._camera_modes
                      if (mode["width"], mode["height"], mode["fps"], mode["fourcc"])
                      == (int(width), int(height), int(fps), str(fourcc))), None)
        if match is None:
            return
        current = (self.settings.camera_width, self.settings.camera_height,
                   self.settings.camera_fps, self.settings.camera_fourcc)
        if current == (width, height, fps, fourcc):
            return
        self.settings.camera_width = int(width)
        self.settings.camera_height = int(height)
        self.settings.camera_fps = int(fps)
        self.settings.camera_fourcc = str(fourcc)
        self.restart_camera()

    def camera_quality_status(self) -> str:
        requested = (self.settings.camera_width, self.settings.camera_height)
        camera = self.camera
        if camera is None or "frame_width" not in camera.actual_mode:
            return "실제 입력 확인 중…"
        mode = camera.actual_mode
        actual = (mode["frame_width"], mode["frame_height"])
        measured_fps = mode.get("measured_fps")
        fps_text = " · %.1f FPS" % measured_fps if measured_fps else " · FPS 측정 중…"
        text = "실제 입력: %d×%d%s" % (actual[0], actual[1], fps_text)
        auto_mode = getattr(camera, "auto_mode", False)
        if auto_mode:
            text += "\n자동 · 장치 기본 출력 사용"
        if not auto_mode and actual != requested:
            text += "\n선택한 해상도가 적용되지 않았습니다."
        if not auto_mode and measured_fps and abs(measured_fps - self.settings.camera_fps) > max(1, self.settings.camera_fps * .15):
            text += "\n카메라가 선택한 %d FPS보다 낮게 전송하고 있습니다." % self.settings.camera_fps
        if camera.output_size:
            text += "\n효과·추적 후: %d×%d" % camera.output_size
        display_fps = mode.get("display_fps")
        if display_fps:
            text += " · 화면 %.1f FPS" % display_fps
            if (measured_fps and measured_fps >= self.settings.camera_fps * .85
                    and display_fps < self.settings.camera_fps * .85):
                text += "\n효과 처리 속도 때문에 화면 FPS가 낮아졌습니다."
        return text

    # -------------------------------------------------------------- 모양 --

    def set_shape(self, shape: str) -> None:
        self.settings.shape = shape
        self.window.update()
        self._notify()

    def toggle_shape(self) -> None:
        self.set_shape(
            SHAPE_RECT if self.settings.shape == SHAPE_ELLIPSE else SHAPE_ELLIPSE
        )

    def set_lock_aspect(self, on: bool) -> None:
        self.settings.lock_aspect = on
        self._notify()

    def set_aspect(self, key: str) -> None:
        self.settings.aspect = key
        ratio = ratio_of(key)
        if ratio is not None:
            # 넓이를 최대한 보존하며 새 비율로 맞춘다 — 창 크기가 튀지 않게.
            geo = self.window.geometry()
            area = max(1, geo.width() * geo.height())
            width, height = fit_to_ratio(
                round((area * ratio) ** 0.5), 0, ratio, prefer_width=True
            )
            self.set_size(width, height)
        self._notify()

    def set_size(self, width: int, height: int) -> None:
        ratio = ratio_of(self.settings.aspect)
        if ratio is not None:
            width, height = fit_to_ratio(width, height, ratio, prefer_width=True)
        geo = self.window.geometry()
        center = geo.center()
        self.window.setGeometry(
            center.x() - width // 2, center.y() - height // 2, width, height
        )
        self._notify()

    def reset_geometry(self) -> None:
        ratio = ratio_of(self.settings.aspect)
        if ratio is None:
            self.settings.w, self.settings.h = 320, 240
        else:
            self.settings.w, self.settings.h = fit_to_ratio(320, 0, ratio, True)
        self.settings.placed = False
        apply_default_placement(self.settings)
        self.window.setGeometry(
            self.settings.x, self.settings.y, self.settings.w, self.settings.h
        )
        self._notify()

    # -------------------------------------------------------------- 보정 --

    def set_skin_smooth(self, value: int) -> None:
        self.settings.skin_smooth = value
        self._notify()

    def set_skin_tone(self, value: int) -> None:
        self.settings.skin_tone = value
        self._notify()

    def set_skin_bright(self, value: int) -> None:
        self.settings.skin_bright = value
        self._notify()

    def reset_retouch(self) -> None:
        self.settings.skin_smooth = 0
        self.settings.skin_tone = 0
        self.settings.skin_bright = 0
        self._notify()

    # -------------------------------------------------------------- 추적 --

    def set_track_enabled(self, on: bool) -> None:
        if bool(on) == self.settings.track_enabled:
            return  # 같은 값으로 다시 눌러도 프레이밍이 튀지 않게
        self.settings.track_enabled = bool(on)
        self.processor.reset_track()  # 켜고 끌 때만 프레이밍을 새로 잡는다
        self._notify()

    def set_track_speed(self, value: int) -> None:
        # reset 을 부르지 않는다 — 슬라이더를 드래그하는 내내 화면이 원위치로
        # 튀면 조절 자체가 불가능하다. 처리기가 매 프레임 이 값을 다시 읽는다.
        self.settings.track_speed = int(value)
        self._notify()

    def set_track_size(self, value: int) -> None:
        self.settings.track_size = int(value)
        self._notify()

    # -------------------------------------------------------------- 배경 --

    def set_bg_mode(self, mode: str) -> None:
        self.settings.bg_mode = mode
        # 마스크 상태만 버린다. 배경을 바꿨다고 프레이밍까지 재조준할 이유는 없다.
        self.processor.reset_mask()
        self._notify()

    def set_bg_blur(self, value: int) -> None:
        self.settings.bg_blur = value
        self._notify()

    def set_bg_image(self, value: str) -> None:
        """`builtin:<키>` 또는 파일 경로."""
        self.settings.bg_image = value
        self.settings.sanitize()  # 사라진 파일을 여기서 걸러낸다
        self._notify()

    def pick_bg_image(self) -> None:
        chosen = ask_image(self.window, self.settings.bg_image)
        if chosen:
            self.set_bg_image(chosen)
            self.set_bg_mode(BG_IMAGE)  # 골랐으면 바로 보여준다

    def set_bg_color(self, color: str) -> None:
        self.settings.bg_color = color
        self._notify()

    def pick_bg_color(self) -> None:
        chosen = ask_color(self.window, self.settings.bg_color, "배경 색상")
        if chosen:
            self.set_bg_color(chosen)

    # -------------------------------------------------------------- 표시 --

    def set_mirror(self, on: bool) -> None:
        self.settings.mirror = on
        self.window.update()
        self._notify()

    def set_border_width(self, width: int) -> None:
        self.settings.border_width = width
        self.window.update()
        self._notify()

    def set_border_color(self, color: str) -> None:
        self.settings.border_color = color
        if self.settings.border_width == 0:
            self.settings.border_width = 2
        self.window.update()
        self._notify()

    def pick_border_color(self) -> None:
        chosen = ask_color(self.window, self.settings.border_color, "테두리 색상")
        if chosen:
            self.set_border_color(chosen)

    def set_opacity(self, percent: int) -> None:
        self.settings.opacity = percent
        self.window.setWindowOpacity(percent / 100.0)
        self._notify()

    def set_always_on_top(self, on: bool) -> None:
        self.settings.always_on_top = on
        was_visible = self.window.isVisible()
        self.window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if was_visible:
            # 플래그를 바꾸면 네이티브 창이 다시 만들어지며 숨겨진다.
            self.window.show()
            self._apply_click_through()
        self._notify()

    # -------------------------------------------------- 클릭 통과 / 숨기기 --

    def _apply_click_through(self) -> None:
        winapi.set_click_through(
            int(self.window.winId()), self.settings.click_through
        )

    def set_click_through(self, on: bool) -> None:
        self.settings.click_through = on
        self._apply_click_through()
        self._notify()
        if on and self.tray is not None and not self._click_through_warned:
            self._click_through_warned = True
            self.tray.showMessage(
                "클릭 통과 켜짐",
                "창을 마우스로 잡을 수 없습니다. Ctrl+Alt+C 또는 트레이 메뉴로 끄세요.",
                self.tray.icon(),
                4000,
            )

    def surface(self) -> None:
        """두 번째 인스턴스가 뜨려 했을 때 — 대신 이미 있는 창을 보여 준다.

        숨겨 둔 상태에서 아이콘을 다시 누른 사용자에게는 아무 반응이 없는 것이
        고장으로 보인다. activateWindow() 까지는 하지 않는다 — 이 앱은 포커스를
        뺏지 않는다는 원칙으로 만들어져 있다(패널의 WA_ShowWithoutActivating).
        """
        self.set_hidden(False)
        self.window.raise_()

    def toggle_visible(self) -> None:
        self.set_hidden(self.window.isVisible())

    def set_hidden(self, hidden: bool) -> None:
        if hidden:
            # 패널은 별개 최상위 창이라 부모를 숨겨도 남는다. 항상 맨 위로 뜬
            # 밝은 카드가 화면에 남으면 "즉시 치우기" 단축키의 의미가 없다.
            self._panel_was_open = self.panel_visible
            self.close_panel()
            self.window.hide()
        else:
            self.window.show()
            self._apply_click_through()
            if self._panel_was_open:
                self._panel_was_open = False
                self.open_panel()


def _install_crash_log() -> None:
    """갑자기 꺼질 때 원인을 남긴다 — %APPDATA%\\WebcamMirror\\crash.log

    콘솔 없는 exe에서는 stderr가 없어서, 슬롯에서 난 파이썬 예외(PySide가
    곧바로 프로세스를 끝낸다)나 네이티브 크래시가 아무 흔적 없이 사라진다.
    faulthandler는 네이티브 크래시 때 모든 스레드의 파이썬 스택을 쓴다.
    """
    import datetime
    import faulthandler
    import os
    import threading
    import traceback

    from settings import APP_DIR

    try:
        os.makedirs(APP_DIR, exist_ok=True)
        path = os.path.join(APP_DIR, "crash.log")
        # 무한히 커지지 않게 크면 새로 시작한다.
        mode = "w" if os.path.exists(path) and os.path.getsize(path) > 512_000 else "a"
        log = open(path, mode, encoding="utf-8", buffering=1)
    except OSError:
        return
    log.write("\n=== start %s v%s ===\n" % (datetime.datetime.now().isoformat(" ", "seconds"),
                                          APP_VERSION))
    faulthandler.enable(log, all_threads=True)
    if sys.stderr is None:
        sys.stderr = log  # PySide가 슬롯 예외 traceback을 여기에 쓴다

    def write_exception(kind, exc_type, exc, tb):
        log.write("[%s] %s\n%s" % (datetime.datetime.now().isoformat(" ", "seconds"), kind,
                                   "".join(traceback.format_exception(exc_type, exc, tb))))

    previous = sys.excepthook

    def excepthook(exc_type, exc, tb):
        write_exception("unhandled", exc_type, exc, tb)
        previous(exc_type, exc, tb)

    sys.excepthook = excepthook
    threading.excepthook = lambda args: write_exception(
        "thread %s" % getattr(args.thread, "name", "?"),
        args.exc_type, args.exc_value, args.exc_traceback)
    # 파일 객체가 GC되면 faulthandler가 닫힌 fd에 쓰게 되므로 붙잡아 둔다.
    sys.modules[__name__]._crash_log = log


def main() -> int:
    # Packaged diagnostic path: exercises dynamic COM imports without opening
    # a video stream, the overlay, or the user's saved settings.
    if len(sys.argv) == 3 and sys.argv[1] == "--camera-mode-report":
        import json
        import traceback
        from pathlib import Path
        report = {"version": APP_VERSION}
        try:
            from camera import _relax_comtypes_typelib_check
            _relax_comtypes_typelib_check()
            from pygrabber.dshow_graph import FilterGraph
            graph = FilterGraph()
            report["devices"] = graph.get_input_devices()
            report["modes"] = {}
            for index, name in enumerate(report["devices"]):
                try:
                    probe = FilterGraph()
                    probe.add_video_input_device(index)
                    report["modes"][name] = probe.get_input_device().get_formats()
                    probe.remove_filters()
                except Exception:
                    report["modes"][name] = {"error": traceback.format_exc()}
        except Exception:
            report["error"] = traceback.format_exc()
        Path(sys.argv[2]).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
        return 1 if "error" in report else 0

    _install_crash_log()
    app = QApplication(sys.argv)
    app.setApplicationName("Webcam Mirror")
    app.setApplicationDisplayName("Webcam Mirror")
    app.setApplicationVersion(APP_VERSION)

    # 중복 실행 차단은 최대한 앞에서 — 테마·아이콘·카메라를 준비하기 전에
    # 물러나야 두 번째 실행이 순식간에 끝나고 웹캠도 건드리지 않는다.
    lock = winapi.SingleInstance(SINGLE_INSTANCE_KEY)
    if not lock.acquired:
        lock.notify_existing()
        return 0

    # 위젯을 만들기 전에 적용해야 한다 — Fusion 으로 바꾸는 게 포함돼 있고,
    # Windows 기본 스타일은 QMenu 의 QSS 상당수를 무시한다.
    theme.apply_theme(app)
    app.setWindowIcon(make_icon())
    app.setQuitOnLastWindowClosed(False)  # 창을 숨겨도 앱은 살아있어야 한다

    controller = AppController(app)
    lock.activated.connect(controller.surface)

    if controller.tray is None:
        QMessageBox.information(
            None,
            "Webcam Mirror",
            "시스템 트레이를 사용할 수 없습니다.\n창에서 우클릭해 메뉴를 여세요.",
        )

    controller.start()
    try:
        return app.exec()
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
