r"""설정 저장/복원 — %APPDATA%\WebcamMirror\settings.json"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields

APP_DIR = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"), "WebcamMirror"
)
SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")

SHAPE_ELLIPSE = "ellipse"
SHAPE_RECT = "rect"
SHAPE_NONE = "none"  # 마스크 없음 — 배경 제거와 함께 쓰면 인물만 떠 있게 된다
SHAPES = (SHAPE_ELLIPSE, SHAPE_RECT, SHAPE_NONE)

MIN_W, MIN_H = 120, 90
MAX_W, MAX_H = 4000, 4000

# (키, 표시이름, 가로/세로 비). None 이면 자유 변형.
ASPECTS = [
    ("free", "자유", None),
    ("1:1", "1:1   정사각", 1 / 1),
    ("4:3", "4:3   웹캠 기본", 4 / 3),
    ("3:4", "3:4   세로", 3 / 4),
    ("16:9", "16:9   와이드", 16 / 9),
    ("9:16", "9:16   숏폼", 9 / 16),
    ("3:2", "3:2   사진", 3 / 2),
    ("2:3", "2:3   사진 세로", 2 / 3),
]
ASPECT_KEYS = tuple(key for key, _, _ in ASPECTS)
_RATIOS = {key: ratio for key, _, ratio in ASPECTS}

# 크기 프리셋은 "긴 변" 하나로만 정의한다. 실제 w x h 는 현재 비율에서 계산되므로
# 비율이 몇 개든 크기 선택지가 모든 비율에서 자동으로 생긴다.
SIZE_STEPS = [
    ("아주 작게", 180),
    ("작게", 240),
    ("보통", 320),
    ("크게", 420),
    ("아주 크게", 560),
    ("최대", 720),
]

BG_OFF = "off"
BG_TRANSPARENT = "transparent"
BG_BLUR = "blur"
BG_COLOR = "color"
BG_IMAGE = "image"
BG_MODES = (BG_OFF, BG_TRANSPARENT, BG_BLUR, BG_COLOR, BG_IMAGE)

# 인물 추적(오토 프레이밍). 속도와 크기를 각각 1~10 으로 조절한다.
#
# 눈금을 선형으로 두면 한쪽 절반이 전부 비슷하게 느껴져서, 내부적으로는
# 비선형으로 편다 — 속도는 시정수를 로그 보간하고(한 칸에 약 35%),
# 크기는 역수를 선형 보간해 화면상 인물 크기가 등간격으로 변하게 한다.
TRACK_SPEED_MIN, TRACK_SPEED_MAX = 1, 10
TRACK_SIZE_MIN, TRACK_SIZE_MAX = 1, 10

TRACK_TAU_SLOW, TRACK_TAU_FAST = 1.20, 0.08  # 초. 속도 1 / 10 의 시정수
TRACK_ZOOM_WIDE, TRACK_ZOOM_TIGHT = 2.60, 1.20  # 인물 높이의 배수. 크기 1 / 10
TRACK_DZ_SLOW, TRACK_DZ_FAST = 0.160, 0.030  # 데드존. 느릴수록 둔감
TRACK_DZ_SIZE_K, TRACK_DZ_SIZE_MAX = 2.6, 0.28


def _track_t(value: int, lo: int, hi: int) -> float:
    return (max(lo, min(hi, int(value))) - lo) / float(hi - lo)


def track_rate(speed: int) -> float:
    """속도 1~10 -> 이징 지수 rate(s^-1). alpha = 1 - exp(-rate*dt) 로 쓴다."""
    t = _track_t(speed, TRACK_SPEED_MIN, TRACK_SPEED_MAX)
    return 1.0 / (TRACK_TAU_SLOW * (TRACK_TAU_FAST / TRACK_TAU_SLOW) ** t)


def track_zoom(size: int) -> float:
    """크기 1~10 -> 크롭 높이 / 인물 높이.

    화면에서 인물이 차지하는 비율은 1/zoom 에 비례하므로, zoom 을 그냥 선형
    보간하면 슬라이더 한쪽 끝에서만 급격히 변한다. 역수 공간에서 보간한다.
    """
    t = _track_t(size, TRACK_SIZE_MIN, TRACK_SIZE_MAX)
    inv = 1.0 / TRACK_ZOOM_WIDE + t * (1.0 / TRACK_ZOOM_TIGHT - 1.0 / TRACK_ZOOM_WIDE)
    return 1.0 / inv


def track_deadzone(speed: int) -> tuple[float, float]:
    """속도 1~10 -> (위치 데드존, 크기 데드존). 빠를수록 예민하다.

    속도와 묶는 게 맞다 — 사용자가 아는 축은 "카메라가 얼마나 부지런히
    따라붙나" 하나이고, 재조준 민감도는 그 축의 일부다.
    """
    t = _track_t(speed, TRACK_SPEED_MIN, TRACK_SPEED_MAX)
    pos = TRACK_DZ_SLOW * (TRACK_DZ_FAST / TRACK_DZ_SLOW) ** t
    return pos, min(TRACK_DZ_SIZE_MAX, TRACK_DZ_SIZE_K * pos)


# --------------------------------------------------------------- 비율 계산 --


def ratio_of(aspect: str) -> float | None:
    """'16:9' -> 1.777... / 'free' -> None"""
    return _RATIOS.get(aspect)


def fit_to_ratio(w: int, h: int, ratio: float, prefer_width: bool = True) -> tuple[int, int]:
    """w x h 를 정확히 `ratio` 로 맞춘다. 최소 크기 보정도 여기서 책임진다.

    비율을 맞춘 결과가 MIN_W/MIN_H 아래로 내려가면 **비율을 유지한 채** 두 변을
    함께 키운다. 한 변만 max() 로 올리면 비율이 깨진다.
    """
    if ratio <= 0:
        return max(MIN_W, w), max(MIN_H, h)

    if prefer_width:
        w2, h2 = w, w / ratio
    else:
        w2, h2 = h * ratio, h

    # 두 변 모두 최소치를 넘길 때까지 비율 그대로 확대
    scale = max(1.0, MIN_W / w2 if w2 else 1.0, MIN_H / h2 if h2 else 1.0)
    w2, h2 = w2 * scale, h2 * scale

    # 최대치도 비율 그대로 축소
    shrink = min(1.0, MAX_W / w2 if w2 > MAX_W else 1.0, MAX_H / h2 if h2 > MAX_H else 1.0)
    w2, h2 = w2 * shrink, h2 * shrink

    return max(MIN_W, round(w2)), max(MIN_H, round(h2))


def size_for(aspect: str, long_edge: int, cur_w: int, cur_h: int) -> tuple[int, int]:
    """크기 프리셋을 현재 비율에 적용한다.

    자유 모드에서는 **지금 창의 비율을 유지한 채** 긴 변만 맞춘다 — 손으로 잡아
    만든 모양이 프리셋을 눌렀다고 망가지지 않게.
    """
    ratio = ratio_of(aspect)
    if ratio is None:
        ratio = cur_w / max(1, cur_h)
    if ratio >= 1:  # 가로가 길다
        return fit_to_ratio(long_edge, 0, ratio, prefer_width=True)
    return fit_to_ratio(0, long_edge, ratio, prefer_width=False)


CAMERA_QUALITIES = {
    "480p": (640, 480),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "2160p": (3840, 2160),
}
QUALITY_LABELS = {"480p": "480p · 640×480", "720p": "HD · 1280×720",
                  "1080p": "Full HD · 1920×1080", "2160p": "4K · 3840×2160"}
CAMERA_FPS_CHOICES = (15, 24, 30, 60)
FPS_LABELS = {fps: "%d FPS" % fps for fps in CAMERA_FPS_CHOICES}


@dataclass
class Settings:
    # 창 위치/크기
    x: int = 0
    y: int = 0
    w: int = 320
    h: int = 240
    placed: bool = False  # False면 최초 실행 → 주 화면 우하단에 배치

    # 모양 / 비율
    shape: str = SHAPE_ELLIPSE
    aspect: str = "free"
    lock_aspect: bool = False  # 자유 모드에서 현재 비율 유지 (Shift 와 동일)

    # 표시
    mirror: bool = True
    opacity: int = 100  # 20~100
    always_on_top: bool = True
    border_color: str = "#ffffff"
    border_width: int = 2
    click_through: bool = False

    # 보정
    skin_smooth: int = 0  # 0~100 피부 매끄럽게
    skin_tone: int = 0  # -50~50 차갑게~따뜻하게
    skin_bright: int = 0  # -50~50 어둡게~밝게

    # 추적
    track_enabled: bool = False
    track_speed: int = 6  # 1~10. 6 이 구버전 이징(0.12/frame @30fps)과 같다
    track_size: int = 5  # 1~10

    # 배경
    bg_mode: str = BG_OFF
    bg_color: str = "#00b140"  # 크로마키 초록
    bg_blur: int = 25  # 1~50
    # "builtin:<키>" 또는 사용자 이미지의 절대 경로
    bg_image: str = "builtin:studio"

    # 카메라
    camera_index: int = 0
    camera_name: str = ""
    camera_quality: str = "720p"
    camera_width: int = 1280
    camera_height: int = 720
    camera_fps: int = 30
    camera_fourcc: str = ""

    # ------------------------------------------------------------------ #

    @classmethod
    def load(cls) -> "Settings":
        """설정 파일을 읽는다. 없거나 손상됐으면 기본값을 돌려준다."""
        s = cls()
        try:
            # utf-8-sig: 메모장 등이 붙인 BOM 이 있어도 읽히게 한다.
            with open(SETTINGS_PATH, encoding="utf-8-sig") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return s
        if not isinstance(raw, dict):
            return s

        for fld in fields(cls):
            if fld.name not in raw:
                continue
            default = getattr(s, fld.name)
            try:
                setattr(s, fld.name, type(default)(raw[fld.name]))
            except (TypeError, ValueError):
                pass  # 값이 이상하면 기본값 유지

        # 구버전 마이그레이션: square 불리언 -> aspect 문자열
        if "aspect" not in raw and raw.get("square"):
            s.aspect = "1:1"

        # 구버전 마이그레이션: track 문자열 -> track_enabled / speed / size
        # 저장된 zoom 배수(2.30/1.75/1.35)를 새 1~10 스케일로 역산한 값이다.
        if "track_enabled" not in raw and "track" in raw:
            old_track = str(raw.get("track") or "off")
            s.track_enabled = old_track != "off"
            s.track_size = {"wide": 2, "normal": 5, "close": 8}.get(old_track, 5)
            s.track_speed = 6  # 구버전 이징과 같은 값

        # 0.1.5 이하: 화질 이름만 저장했다. 실제 크기 필드로 옮긴다.
        if "camera_width" not in raw or "camera_height" not in raw:
            s.camera_width, s.camera_height = CAMERA_QUALITIES.get(
                s.camera_quality, CAMERA_QUALITIES["720p"])

        s.sanitize()
        return s

    def save(self) -> None:
        """원자적 쓰기. 실패해도 앱은 계속 동작한다."""
        try:
            os.makedirs(APP_DIR, exist_ok=True)
            tmp = SETTINGS_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, ensure_ascii=False, indent=2)
            os.replace(tmp, SETTINGS_PATH)
        except OSError:
            pass

    def sanitize(self) -> None:
        self.w = max(MIN_W, min(MAX_W, int(self.w)))
        self.h = max(MIN_H, min(MAX_H, int(self.h)))
        self.opacity = max(20, min(100, int(self.opacity)))
        self.border_width = max(0, min(8, int(self.border_width)))
        if self.shape not in SHAPES:
            self.shape = SHAPE_ELLIPSE
        if self.aspect not in ASPECT_KEYS:
            self.aspect = "free"
        if self.camera_index < 0:
            self.camera_index = 0
        if self.camera_quality not in CAMERA_QUALITIES:
            self.camera_quality = "720p"
        self.camera_width = max(160, min(7680, int(self.camera_width)))
        self.camera_height = max(120, min(4320, int(self.camera_height)))
        self.camera_fps = max(1, min(240, int(self.camera_fps)))
        self.camera_fourcc = str(self.camera_fourcc or "")[:4]
        self.skin_smooth = max(0, min(100, int(self.skin_smooth)))
        self.skin_tone = max(-50, min(50, int(self.skin_tone)))
        self.skin_bright = max(-50, min(50, int(self.skin_bright)))
        if self.bg_mode not in BG_MODES:
            self.bg_mode = BG_OFF
        self.track_enabled = bool(self.track_enabled)
        self.track_speed = max(
            TRACK_SPEED_MIN, min(TRACK_SPEED_MAX, int(self.track_speed))
        )
        self.track_size = max(TRACK_SIZE_MIN, min(TRACK_SIZE_MAX, int(self.track_size)))
        self.bg_blur = max(1, min(50, int(self.bg_blur)))
        # 파일이 사라졌거나(외장 드라이브·정리) 값이 망가졌으면 기본 배경으로
        # 돌린다. 여기서 안 막으면 캡처 스레드에서 매 프레임 디코딩을 시도한다.
        import backgrounds

        image = self.bg_image if isinstance(self.bg_image, str) else ""
        if backgrounds.is_builtin(image):
            self.bg_image = backgrounds.PREFIX + backgrounds.builtin_key(image)
        elif not (image and os.path.isfile(image)):
            self.bg_image = backgrounds.PREFIX + backgrounds.DEFAULT

        ratio = ratio_of(self.aspect)
        if ratio is not None:
            self.w, self.h = fit_to_ratio(self.w, self.h, ratio, prefer_width=True)

        # 손으로 고친 "#fff" 같은 짧은 hex 가 들어오면 색을 파싱하다 예외가 나고,
        # 그 예외가 캡처 스레드를 통째로 죽인다. 길이까지 확인한다.
        for name, fallback in (("border_color", "#ffffff"), ("bg_color", "#00b140")):
            value = getattr(self, name)
            valid = (
                isinstance(value, str)
                and len(value) == 7
                and value.startswith("#")
                and all(c in "0123456789abcdefABCDEF" for c in value[1:])
            )
            if not valid:
                setattr(self, name, fallback)


def apply_default_placement(s: Settings) -> None:
    """저장된 좌표가 현재 모니터 구성에서 보이지 않으면 주 화면 우하단으로 되돌린다.

    QGuiApplication 생성 이후에 호출해야 한다.
    """
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QGuiApplication

    screens = QGuiApplication.screens()
    if not screens:
        return

    if s.placed:
        rect = QRect(s.x, s.y, s.w, s.h)
        for scr in screens:
            visible = scr.availableGeometry().intersected(rect)
            if visible.width() >= 60 and visible.height() >= 40:
                return  # 충분히 보인다 — 그대로 사용

    primary = QGuiApplication.primaryScreen()
    if primary is None:
        primary = screens[0]
    g = primary.availableGeometry()
    s.x = g.right() - s.w - 40
    s.y = g.bottom() - s.h - 40
    s.placed = True
