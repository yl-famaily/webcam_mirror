"""Windows 전용 ctypes 헬퍼 — 클릭 통과와 전역 단축키.

Qt의 WA_TransparentForMouseEvents는 위젯 내부에만 적용돼 OS 레벨에서
마우스가 통과하지 않는다. 그래서 WS_EX_TRANSPARENT를 직접 붙인다.
전역 단축키는 keyboard 패키지(관리자 권한 요구) 대신 RegisterHotKey를 쓴다.
중복 실행 차단(SingleInstance)도 여기 둔다 — 잠금 파일과 명명된 이벤트가 ctypes 이고,
HotkeyManager 처럼 Qt 이벤트 루프와 맞물리는 Windows 헬퍼이기 때문이다.
"""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, QTimer, Signal

user32 = ctypes.windll.user32
# GetLastError 를 믿으려면 use_last_error 로 열어야 한다. ctypes 가 호출 사이에
# 다른 Win32 를 부르면 windll 쪽 GetLastError 는 이미 덮어써져 있을 수 있다.
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

VK_C = 0x43
VK_F = 0x46
VK_M = 0x4D
VK_S = 0x53
VK_T = 0x54

# 64비트에는 ...LongPtrW, 32비트에는 ...LongW만 존재한다.
if hasattr(user32, "SetWindowLongPtrW"):
    _get_style = user32.GetWindowLongPtrW
    _set_style = user32.SetWindowLongPtrW
else:  # pragma: no cover - 32비트 파이썬
    _get_style = user32.GetWindowLongW
    _set_style = user32.SetWindowLongW

_get_style.restype = ctypes.c_ssize_t
_get_style.argtypes = [wintypes.HWND, ctypes.c_int]
_set_style.restype = ctypes.c_ssize_t
_set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]

EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_ALWAYS = 4
FILE_ATTRIBUTE_HIDDEN = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenEventW.restype = wintypes.HANDLE
kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.SetEvent.restype = wintypes.BOOL
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

user32.RegisterHotKey.restype = wintypes.BOOL
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]


def set_click_through(hwnd: int, enabled: bool) -> None:
    """마우스 이벤트가 창을 통과해 뒤쪽 창으로 전달되게 한다."""
    if not hwnd:
        return
    handle = wintypes.HWND(int(hwnd))
    style = _get_style(handle, GWL_EXSTYLE)
    if enabled:
        style |= WS_EX_LAYERED | WS_EX_TRANSPARENT
    else:
        # WS_EX_LAYERED는 반투명 렌더링에 필요하므로 남겨둔다.
        style &= ~WS_EX_TRANSPARENT
    _set_style(handle, GWL_EXSTYLE, style)


class HotkeyManager(QAbstractNativeEventFilter):
    """RegisterHotKey로 등록하고 WM_HOTKEY를 Qt 이벤트 루프에서 받는다."""

    def __init__(self) -> None:
        super().__init__()
        self._callbacks: dict[int, Callable[[], None]] = {}
        self._next_id = 1
        self.failed: list[str] = []

    def register(self, mods: int, vk: int, callback: Callable[[], None], label: str = "") -> bool:
        hotkey_id = self._next_id
        if not user32.RegisterHotKey(None, hotkey_id, mods | MOD_NOREPEAT, vk):
            self.failed.append(label)  # 다른 앱과 충돌 — 조용히 넘어간다
            return False
        self._callbacks[hotkey_id] = callback
        self._next_id += 1
        return True

    def nativeEventFilter(self, event_type, message):
        if event_type != b"windows_generic_MSG":
            return False, 0
        msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
        if msg.message == WM_HOTKEY:
            callback = self._callbacks.get(int(msg.wParam))
            if callback is not None:
                callback()
                return True, 0
        return False, 0

    def unregister_all(self) -> None:
        for hotkey_id in self._callbacks:
            user32.UnregisterHotKey(None, hotkey_id)
        self._callbacks.clear()


class SingleInstance(QObject):
    """앱이 한 번에 하나만 돌게 한다.

    두 벌이 동시에 뜨면 같은 카메라를 두고 다투다 한쪽이 "장치를 열 수 없다"로
    죽고, 살아남은 쪽도 겹쳐 뜬 창 두 개가 서로를 가려 뭐가 뭔지 알 수 없다.
    설정 파일도 양쪽이 번갈아 덮어써 마지막에 종료한 쪽만 남는다.

    **판정은 공유를 금지한 잠금 파일로 한다.** PyInstaller onefile 은 부트로더
    부모와 실제 앱 자식이 별도 프로세스로 뜬다. 명명된 뮤텍스는 이 전환에서
    인스턴스 판정이 불안정했지만, 공유 모드 0인 CreateFileW 핸들은 실제 앱이
    살아 있는 동안 다른 프로세스가 같은 파일을 열 수 없어 안정적이다. 파일이
    디스크에 남아도 잠금은 핸들에만 걸리므로 비정상 종료 후 재실행도 막지 않는다.

    **알림은 명명된 Win32 이벤트로 한다.** 그냥 조용히 죽으면, 트레이에 숨어 있는 앱을
    다시 실행한 사용자 눈에는 아이콘을 눌러도 아무 일도 안 일어나는 것으로만
    보인다. 그래서 두 번째 인스턴스는 죽기 전에 첫 번째에게 "네가 나와라"를
    보내고, 첫 번째가 `activated` 로 그걸 받아 창을 띄운다.
    """

    activated = Signal()

    CONNECT_TIMEOUT_MS = 1000  # 첫 인스턴스가 이벤트를 만들 때까지의 최대 대기

    def __init__(self, key: str, parent=None) -> None:
        super().__init__(parent)
        self._key = key
        self._lock_file = None
        self._event = None
        self._event_name = "Local\\" + key + "-Activate"
        self._poll = QTimer(self)
        self._poll.setInterval(50)
        self._poll.timeout.connect(self._check_activation)

        app_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.getcwd()), "WebcamMirror")
        os.makedirs(app_dir, exist_ok=True)
        lock_path = os.path.join(app_dir, key + ".lock")
        lock_file = kernel32.CreateFileW(
            lock_path,
            GENERIC_READ | GENERIC_WRITE,
            0,  # 다른 프로세스의 읽기·쓰기·삭제를 모두 막는다
            None,
            OPEN_ALWAYS,
            FILE_ATTRIBUTE_HIDDEN,
            None,
        )

        if lock_file != INVALID_HANDLE_VALUE:
            self._lock_file = lock_file
            self.acquired = True
            self._event = kernel32.CreateEventW(None, False, False, self._event_name)
            if self._event:
                self._poll.start()
        else:
            self.acquired = False

    def _check_activation(self) -> None:
        if self._event and kernel32.WaitForSingleObject(self._event, 0) == WAIT_OBJECT_0:
            self.activated.emit()

    def notify_existing(self) -> bool:
        """두 번째 인스턴스가 죽기 전에 첫 번째를 깨운다."""
        deadline = time.monotonic() + self.CONNECT_TIMEOUT_MS / 1000.0
        while time.monotonic() < deadline:
            event = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, self._event_name)
            if event:
                try:
                    return bool(kernel32.SetEvent(event))
                finally:
                    kernel32.CloseHandle(event)
            time.sleep(0.02)
        return False

    def release(self) -> None:
        self._poll.stop()
        if self._event:
            kernel32.CloseHandle(self._event)
            self._event = None
        if self._lock_file and self._lock_file != INVALID_HANDLE_VALUE:
            kernel32.CloseHandle(self._lock_file)
            self._lock_file = None
