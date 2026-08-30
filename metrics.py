"""마스크 품질 측정 — 정답 없이 쓸 수 있는 지표 3종.

예전에 쓰던 "경계 픽셀의 평균 이미지 그래디언트"는 두 가지가 잘못됐다.
정규화가 안 돼 있어 마스크가 어수선한 배경 쪽으로 밀려도 점수가 올랐고,
**시간축 불안정에 완전히 눈이 멀어서** 외곽선이 매 프레임 깜빡이는 버그를
한 번도 잡아내지 못했다. 아래 세 지표가 그 두 구멍을 메운다.

    python metrics.py <클립.npy>      # 프레임 묶음에 대해 전체 지표 출력
"""

from __future__ import annotations

import cv2
import numpy as np

BAND_LO, BAND_HI = 0.05, 0.95  # 소프트 경계로 볼 알파 구간


# ------------------------------------------------------------ 공간 지표 --


def gradient_alignment(alpha: np.ndarray, bgr: np.ndarray, tau: float = 0.02) -> float:
    """알파 경계가 실제 이미지 윤곽을 '따라가는' 정도. 0~1, 높을수록 좋다.

    알파 그래디언트 방향과 이미지 그래디언트 방향의 내적을 알파 그래디언트
    크기로 가중 평균한다. 정규화돼 있으므로 어수선한 곳으로 경계가 밀려도
    점수가 오르지 않는다 — 방향이 맞아야만 오른다.
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    ax = cv2.Sobel(alpha, cv2.CV_32F, 1, 0, 3)
    ay = cv2.Sobel(alpha, cv2.CV_32F, 0, 1, 3)
    ix = cv2.Sobel(gray, cv2.CV_32F, 1, 0, 3)
    iy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, 3)

    amag = cv2.magnitude(ax, ay)
    imag = cv2.magnitude(ix, iy)
    sel = amag > tau
    if not sel.any():
        return 0.0

    dot = np.abs(ax[sel] * ix[sel] + ay[sel] * iy[sel])
    denom = np.maximum(amag[sel] * imag[sel], 1e-6)
    cos = np.clip(dot / denom, 0.0, 1.0)
    return float(np.average(cos, weights=amag[sel]))


def boundary_displacement(alpha: np.ndarray, bgr: np.ndarray) -> tuple[float, float]:
    """alpha=0.5 등고선에서 가장 가까운 Canny 엣지까지의 거리(px).

    (중앙값, 90퍼센타일). "마스크가 1.4px 어긋나 있다"로 바로 읽힌다.
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 160)
    # 엣지까지의 거리장 — 엣지가 0, 멀수록 큼
    dist = cv2.distanceTransform((edges == 0).astype(np.uint8), cv2.DIST_L2, 3)

    binary = (alpha > 0.5).astype(np.uint8)
    contour = cv2.morphologyEx(binary, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
    if not contour.any():
        return (float("nan"), float("nan"))
    d = dist[contour]
    return (float(np.median(d)), float(np.percentile(d, 90)))


def soft_band_ratio(alpha: np.ndarray) -> float:
    """애매한(0.05~0.95) 픽셀 비율. 낮다고 무조건 좋은 건 아니다 —
    머리카락에는 이 밴드가 필요하다. 다른 지표와 함께 읽을 것."""
    return float(np.mean((alpha > BAND_LO) & (alpha < BAND_HI)))


# ------------------------------------------------------------ 시간 지표 --


class TemporalError:
    """광류로 보정한 프레임 간 알파 변화량.

    보정 없이 |a_t - a_{t-1}| 만 재면 '진짜 움직임'과 '깜빡임'이 섞인다.
    직전 알파를 광류로 워프한 뒤 비교하면 불안정만 남는다.
    """

    def __init__(self) -> None:
        self._flow = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_ULTRAFAST)
        self._prev_gray: np.ndarray | None = None
        self._prev_alpha: np.ndarray | None = None
        self.warped: list[float] = []
        self.raw: list[float] = []

    def update(self, alpha: np.ndarray, bgr: np.ndarray) -> None:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        if self._prev_gray is not None and self._prev_alpha is not None:
            self.raw.append(float(np.mean(np.abs(alpha - self._prev_alpha))))

            flow = self._flow.calc(self._prev_gray, gray, None)
            h, w = gray.shape
            grid_x, grid_y = np.meshgrid(np.arange(w, dtype=np.float32),
                                         np.arange(h, dtype=np.float32))
            map_x = grid_x + flow[:, :, 0]
            map_y = grid_y + flow[:, :, 1]
            warped = cv2.remap(self._prev_alpha, map_x, map_y,
                               cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            self.warped.append(float(np.mean(np.abs(alpha - warped))))

        self._prev_gray = gray
        self._prev_alpha = alpha

    def summary(self) -> dict[str, float]:
        return {
            "temporal_warped": float(np.mean(self.warped)) if self.warped else float("nan"),
            "temporal_raw": float(np.mean(self.raw)) if self.raw else float("nan"),
        }


# ------------------------------------------------------------- 정답 대조 --


def trimap_from_alpha(alpha: np.ndarray, band: int = 4) -> np.ndarray:
    """정답 알파에서 ±band 픽셀의 불확실 영역만 뽑는다. 평가는 여기서만 한다."""
    k = np.ones((band * 2 + 1, band * 2 + 1), np.uint8)
    solid = (alpha > 0.5).astype(np.uint8)
    return (cv2.dilate(solid, k) - cv2.erode(solid, k)) > 0


def sad_mad(alpha: np.ndarray, truth: np.ndarray, band: np.ndarray) -> tuple[float, float]:
    """trimap 밴드 안에서의 SAD / MAD."""
    if not band.any():
        return (float("nan"), float("nan"))
    diff = np.abs(alpha[band] - truth[band])
    return (float(diff.sum()), float(diff.mean()))


# ------------------------------------------------------------------ CLI --


def evaluate(frames: np.ndarray, masks: list[np.ndarray]) -> dict[str, float]:
    """프레임 묶음과 그에 대응하는 알파 목록으로 전체 지표를 낸다."""
    temporal = TemporalError()
    align, med, p90, band = [], [], [], []
    for frame, alpha in zip(frames, masks):
        alpha = alpha.astype(np.float32)
        align.append(gradient_alignment(alpha, frame))
        m, p = boundary_displacement(alpha, frame)
        med.append(m)
        p90.append(p)
        band.append(soft_band_ratio(alpha))
        temporal.update(alpha, frame)

    out = {
        "gradient_alignment": float(np.nanmean(align)),
        "boundary_median_px": float(np.nanmean(med)),
        "boundary_p90_px": float(np.nanmean(p90)),
        "soft_band_ratio": float(np.nanmean(band)),
    }
    out.update(temporal.summary())
    return out


def report(name: str, scores: dict[str, float]) -> str:
    lines = ["[%s]" % name]
    labels = {
        "gradient_alignment": "윤곽 방향 일치 (높을수록 좋음)",
        "boundary_median_px": "경계 어긋남 중앙값 px (낮을수록)",
        "boundary_p90_px": "경계 어긋남 90% px (낮을수록)",
        "soft_band_ratio": "소프트 밴드 비율 (참고)",
        "temporal_warped": "시간축 오차 광류보정 (낮을수록)",
        "temporal_raw": "시간축 오차 원시 (참고)",
    }
    for key, label in labels.items():
        if key in scores:
            lines.append("  %-34s %.4f" % (label, scores[key]))
    return "\n".join(lines)
