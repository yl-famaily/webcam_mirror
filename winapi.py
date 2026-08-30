"""Windows 전용 ctypes 헬퍼 — 클릭 통과와 전역 단축키.

Qt의 WA_TransparentForMouseEvents는 위젯 내부에만 적용돼 OS 레벨에서
마우스가 통과하지 않는다. 그래서 WS_EX_TRANSPARENT를 직접 붙인다.
전역 단축키는 keyboard 패키지(관리자 권한 요구) 대신 RegisterHotKey를 쓴다.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter

user32 = ctypes.windll.user32

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
