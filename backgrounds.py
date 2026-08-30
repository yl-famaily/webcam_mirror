"""기본 배경 이미지 — 파일이 아니라 **코드로 그린다**.

스톡 사진을 번들하지 않는 이유는 라이선스다. Microsoft Store 배포는 상업적
재배포라, 사진마다 그게 허용되는 라이선스인지 증명할 수 있어야 한다. 무료로
받은 이미지도 대부분 재배포는 막혀 있다. 코드로 만들면 그 문제가 아예 없고,
리포지토리에 바이너리도 안 들어가며, 어떤 해상도로도 선명하게 나온다.

전부 BGR uint8 을 돌려준다 (OpenCV 관례). 난수는 고정 시드라 같은 크기면
언제나 같은 그림이 나온다 — 프레임마다 배경이 달라지면 안 되니까.
"""

from __future__ import annotations

import cv2
import numpy as np

# (키, 표시 이름). 패널의 썸네일 순서가 이 순서다.
BUILTINS = (
    ("studio", "스튜디오"),
    ("bokeh", "보케"),
    ("blue", "블루"),
    ("sunset", "노을"),
    ("wall", "화이트월"),
)
BUILTIN_KEYS = tuple(key for key, _ in BUILTINS)
DEFAULT = "studio"

PREFIX = "builtin:"


def is_builtin(value: str) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def builtin_key(value: str) -> str:
    key = value[len(PREFIX):] if is_builtin(value) else value
    return key if key in BUILTIN_KEYS else DEFAULT


def _grid(w: int, h: int):
    """0~1 로 정규화한 x, y 좌표판. 모든 그라디언트의 재료다."""
    y, x = np.mgrid[0:h, 0:w]
    return x / max(1.0, w - 1.0), y / max(1.0, h - 1.0)


def _linear(w: int, h: int, c0, c1, angle: float = 0.5):
    """c0 -> c1 선형 그라디언트. angle 0=가로, 1=세로, 0.5=대각."""
    x, y = _grid(w, h)
    t = (x * (1.0 - angle) + y * angle)[:, :, None]
    a = np.array(c0, np.float32)[::-1]  # RGB 로 적고 BGR 로 뒤집는다
    b = np.array(c1, np.float32)[::-1]
    return a + (b - a) * t


def _vignette(w: int, h: int, strength: float = 0.75, cy: float = 0.42):
    """가장자리로 갈수록 어두워지는 계수판 (1.0 = 그대로)."""
    x, y = _grid(w, h)
    # 화면 비율을 반영해야 원형으로 보인다
    dx = (x - 0.5) * (w / max(1.0, h))
    dy = y - cy
    r = np.sqrt(dx * dx + dy * dy) / 0.75
    return np.clip(1.0 - strength * np.clip(r, 0.0, 1.6) ** 2, 0.08, 1.0)[:, :, None]


def _studio(w: int, h: int):
    """인물 사진관의 배경천. 가운데가 밝고 가장자리로 떨어진다."""
    base = _linear(w, h, (124, 130, 144), (44, 47, 58), angle=0.85)
    return base * _vignette(w, h, 0.52)


def _blue(w: int, h: int):
    return _linear(w, h, (59, 130, 246), (11, 27, 58), angle=0.55) * _vignette(w, h, 0.35)


def _sunset(w: int, h: int):
    x, y = _grid(w, h)
    t = y[:, :, None]
    # 3색 보간 — 2색만으로는 노을 느낌이 안 난다
    top = np.array((250, 176, 94), np.float32)[::-1]
    mid = np.array((214, 96, 122), np.float32)[::-1]
    bot = np.array((58, 38, 92), np.float32)[::-1]
    lower = t * 2.0
    upper = (t - 0.5) * 2.0
    out = np.where(t < 0.5, top + (mid - top) * lower, mid + (bot - mid) * upper)
    return out * _vignette(w, h, 0.28, cy=0.5)


def _wall(w: int, h: int):
    """밝은 실내 벽. 어두운 배경만 있으면 밝은 옷차림에서 인물이 묻힌다."""
    base = _linear(w, h, (238, 236, 231), (198, 194, 186), angle=0.9)
    return base * _vignette(w, h, 0.18, cy=0.35)


def _bokeh(w: int, h: int):
    """초점 나간 실내 조명. 원을 그린 뒤 통째로 흐려서 만든다."""
    out = _linear(w, h, (26, 32, 58), (8, 10, 22), angle=0.7)
    layer = np.zeros((h, w, 3), np.float32)
    rng = np.random.default_rng(20240827)  # 고정 시드 — 매번 같은 그림이어야 한다
    unit = max(w, h)
    # 색을 옅게 쓴다 — 쨍한 원색을 쓰면 초점 나간 조명이 아니라 색종이가 된다.
    tints = ((255, 226, 186), (196, 214, 250), (240, 208, 214), (214, 240, 228))
    for _ in range(18):
        cx = int(rng.uniform(-0.05, 1.05) * w)
        cy = int(rng.uniform(-0.05, 1.05) * h)
        radius = int(rng.uniform(0.028, 0.075) * unit)
        tint = tints[int(rng.integers(0, len(tints)))]
        color = tuple(float(c) * rng.uniform(0.25, 0.62) for c in tint[::-1])
        cv2.circle(layer, (cx, cy), radius, color, -1, lineType=cv2.LINE_AA)
    # 흐림 반경을 원 크기에 비례시켜야 어떤 해상도에서도 같아 보인다
    layer = cv2.GaussianBlur(layer, (0, 0), unit * 0.030)
    return np.clip(out + layer * 0.95, 0, 255)


_MAKERS = {
    "studio": _studio,
    "bokeh": _bokeh,
    "blue": _blue,
    "sunset": _sunset,
    "wall": _wall,
}


def render(key: str, w: int, h: int) -> np.ndarray:
    """기본 배경 하나를 w x h BGR uint8 로 그린다."""
    w, h = max(2, int(w)), max(2, int(h))
    maker = _MAKERS.get(builtin_key(key), _studio)
    return np.clip(maker(w, h), 0, 255).astype(np.uint8)


def load_file(path: str, w: int, h: int) -> np.ndarray | None:
    """사용자 이미지를 읽어 w x h 를 꽉 채우도록 잘라 맞춘다.

    `cv2.imread` 를 쓰면 안 된다 — 경로에 한글이 있으면 조용히 None 을
    돌려준다. 사용자 이름이 한글인 Windows 계정에서는 바탕화면·다운로드 폴더가
    전부 여기에 해당한다. 바이트로 읽어 `imdecode` 로 넘긴다.
    """
    try:
        raw = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    if raw.size == 0:
        return None
    img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if img is None:
        return None
    return cover(img, w, h)


def cover(img: np.ndarray, w: int, h: int) -> np.ndarray:
    """비율을 유지한 채 w x h 를 꽉 채우고 넘치는 쪽을 가운데 기준으로 자른다."""
    w, h = max(2, int(w)), max(2, int(h))
    ih, iw = img.shape[:2]
    scale = max(w / iw, h / ih)
    rw, rh = max(w, int(round(iw * scale))), max(h, int(round(ih * scale)))
    # 축소는 INTER_AREA 가 계단을 덜 만든다
    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    resized = cv2.resize(img, (rw, rh), interpolation=interp)
    x0 = (rw - w) // 2
    y0 = (rh - h) // 2
    return np.ascontiguousarray(resized[y0:y0 + h, x0:x0 + w])
