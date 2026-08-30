"""프레임 후처리 — 피부톤 보정과 배경 제거.

캡처 스레드 안에서 돌아가므로 비용에 민감하다. 1280x720 원본을 그대로
필터링하면 30fps 를 못 맞추므로(bilateral 기준 18ms), 작업 해상도로
줄인 뒤 처리한다. 창은 보통 320x240 근처라 640 폭이면 충분히 선명하다.
"""

from __future__ import annotations

import math
import os
import sys
import time

import cv2
import numpy as np

import backgrounds

from settings import (
    BG_BLUR,
    BG_COLOR,
    BG_IMAGE,
    BG_OFF,
    BG_TRANSPARENT,
    track_deadzone,
    track_rate,
    track_zoom,
)

WORK_WIDTH = 640  # 효과를 적용할 작업 해상도(가로)

# YCrCb 색공간의 일반적인 피부색 범위. 조명 변화에 비교적 둔감하다.
SKIN_LOW = np.array([0, 133, 77], dtype=np.uint8)
SKIN_HIGH = np.array([255, 173, 127], dtype=np.uint8)

# 보정 강도 프리셋 (메뉴에서 고르는 값)
SMOOTH_LEVELS = [("없음", 0), ("약하게", 35), ("보통", 60), ("강하게", 85)]
# 55 는 Settings.sanitize() 의 -50~50 밖이라 재시작하면 50 으로 잘리고,
# 메뉴의 값 비교가 어디에도 안 맞아 라디오 체크가 통째로 사라졌었다.
TONE_LEVELS = [("차갑게", -30), ("기본", 0), ("따뜻하게", 30), ("많이 따뜻하게", 50)]
BRIGHT_LEVELS = [("어둡게", -25), ("기본", 0), ("밝게", 25), ("많이 밝게", 45)]
BLUR_LEVELS = [("약하게", 12), ("보통", 25), ("강하게", 40)]

# MediaPipe Selfie Segmentation (Google 공식 배포본, Apache 2.0).
# 입력 256x256 고정, 출력 (1, 1, 256, 256) 의 인물 확률.
#
# 처음에는 OpenCV Zoo 의 PPHumanSeg 를 썼는데, 그 모델은 상반신 인물 사진으로
# 학습돼 얼굴이 화면을 가득 채우는 웹캠 클로즈업에서 마스크가 얼굴을 파먹었다.
# 이 모델은 웹캠 셀피가 주 용도라 같은 조건에서 애매한 경계 비율이
# 0.313 -> 0.019 로 떨어진다. 용량도 5.9MB -> 244KB.
SEG_INPUT = 256
MODEL_NAME = "selfie_seg.tflite"
ONNX_NAME = "selfie_seg.onnx"

# 출력이 이미 선명해서 예전만큼 세게 다듬을 필요가 없다. 살짝만 굳힌다.
MASK_GAIN = 1.2  # 대비 커브 기울기 (가이디드 필터가 경계를 잡아주므로 약하게)
MASK_MID = 0.5
TEMPORAL_MAX = 0.75  # 정지 영역에서 직전 마스크에 줄 최대 가중치
TEMPORAL_MOTION = 0.25  # 이만큼 변한 픽셀은 직전 값을 아예 안 쓴다
BLOB_KEEP_RATIO = 0.2  # 가장 큰 덩어리의 이 비율 미만인 조각은 오검출로 보고 버린다

# 추론이 프레임당 17ms 라 보정까지 켜면 30fps(33ms) 를 깰 수 있다. 그래서
# 실제 처리 시간을 재서 스스로 조절한다 — 여유가 있으면 매 프레임 추론해
# 움직임을 정확히 따라가고, 빠듯해지면 격프레임으로 내려가 직전 마스크를
# 재사용한다. 두 문턱 사이를 벌려 경계에서 왔다갔다하지 않게 한다.
SEG_SLOW_MS = 30.0  # 프레임 전체가 이 위로 올라가면 격프레임으로 내려간다
SEG_FAST_MS = 22.0  # 이 아래로 내려오면 다시 매 프레임
SEG_WORTH_SKIPPING_MS = 5.0  # 세그멘테이션이 이보다 싸면 건너뛰어도 소용없다
SEG_EVERY_MAX = 3  # 여기까지 늘릴 수 있다 (느린 PC 대비)
SEG_SETTLE_FRAMES = 20  # 주기를 바꾼 뒤 EMA 가 따라올 때까지 쉬는 프레임

# 인물 추적(오토 프레이밍). 이징 속도와 데드존은 settings 의 속도 1~10 에서
# 파생된다 — 여기 하드코딩하면 슬라이더가 의미를 잃는다.
TRACK_SIZE_RATE_K = 0.5  # 크기는 위치의 절반 속도로 따라가야 줌이 안 들썩인다
TRACK_HEAD_BIAS = 0.45  # 인물 상자에서 크롭 중심을 잡는 위치 (얼굴이 살짝 위)
TRACK_DT_MIN, TRACK_DT_MAX = 1.0 / 240, 0.25  # 상한은 앱이 멈췄다 깨어날 때 대비


def asset_path(name: str) -> str:
    """PyInstaller 로 묶였을 때(_MEIPASS)와 스크립트 실행을 모두 지원한다."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", name)


def model_path() -> str:
    return asset_path(MODEL_NAME)


def model_available() -> bool:
    """둘 중 하나만 있어도 배경 제거가 된다."""
    return os.path.isfile(asset_path(ONNX_NAME)) or os.path.isfile(model_path())


class FrameProcessor:
    """설정을 참조해 캡처된 BGR 프레임을 보정한다.

    배경 제거를 켜면 BGRA(4채널)를 돌려줄 수 있다 — 투명 배경 모드.
    """

    def __init__(self, settings):
        self.s = settings
        self._infer = None
        self._net_failed = False
        self.backend = "none"
        self._prev_mask = None
        self._cached_mask = None
        self._bg_image = None  # 배경 이미지 캐시 (프레임마다 디코딩하면 안 된다)
        self._bg_image_key = None
        self._frame_no = 0
        self._cost_ms = 0.0  # 프레임 전체 처리 시간 EMA
        self._seg_cost_ms = 0.0  # 추론 1회 비용 EMA (건너뛴 프레임은 안 섞는다)
        self._seg_every = 1
        self._settle = 0  # 주기를 바꾼 뒤 EMA 가 따라올 때까지 기다리는 프레임 수
        self._warmed = False  # 첫 프레임(모델 로딩 포함)을 버렸는지
        self._track_cur = None  # 지금 화면에 적용 중인 크롭 (중심x, 중심y, 폭, 높이)
        self._track_goal = None  # 따라가는 중인 목표 크롭
        self._last_t = None  # 직전 프레임 시각 — dt 기반 이징에 쓴다
        self._track_key = None  # 크기/비율 설정 스냅샷 — 바뀌면 데드존을 건너뛴다
        self._track_forced = False

    # ------------------------------------------------------------- 진입점 --

    def enabled(self) -> bool:
        return bool(
            self.s.skin_smooth
            or self.s.skin_tone
            or self.s.skin_bright
            or self.s.bg_mode != BG_OFF
            or self.s.track_enabled
        )

    def process(self, bgr: np.ndarray) -> np.ndarray:
        if not self.enabled():
            return bgr
        started = time.perf_counter()
        work = self._downscale(bgr)
        # 모델은 보정되지 않은 원본을 봐야 한다. 보정본은 bilateral 블러에
        # 밝기 +-27, R/B 시프트 +-16 이 걸린 이미지라 학습 분포에서 벗어난다.
        clean = work

        # 마스크는 한 번만 뽑아 배경 제거와 추적이 나눠 쓴다. 추적만 켜도
        # 인물 위치를 알아야 하므로 세그멘테이션이 필요하다.
        tracking = self.s.track_enabled
        removing = self.s.bg_mode != BG_OFF
        mask = self._person_mask(clean) if (tracking or removing) else None

        if self.s.skin_smooth:
            work = self._smooth_skin(work, self.s.skin_smooth)
        if self.s.skin_tone or self.s.skin_bright:
            work = self._adjust_tone(work, self.s.skin_tone, self.s.skin_bright)
        if removing:
            work = self._apply_background(work, mask)
        if tracking:
            work = self._apply_tracking(work, mask)

        self._tune_cadence((time.perf_counter() - started) * 1000.0)
        return work

    def _tune_cadence(self, total_ms: float) -> None:
        """추론 주기를 스스로 조절한다.

        지켜야 하는 건 **프레임 전체** 예산이므로 판단은 total 로 한다. 다만
        물러서서 이득을 보는 건 세그멘테이션을 건너뛸 때뿐이라, 세그멘테이션이
        비용의 의미 있는 몫을 차지할 때만 주기를 늘린다. (피부 보정 때문에
        느린 건데 추론을 건너뛰어봐야 소용없다.)
        """
        if not self._cost_ms:
            # 첫 프레임에는 모델 로딩(onnxruntime 세션 생성, 약 117ms)이 섞여
            # 있다. 씨앗으로 쓰면 EMA 가 20프레임을 지나서도 살아남아 주기를
            # 한 번 튀게 만든다. 그래서 아예 버리고 두 번째 프레임으로 시작한다.
            if not self._warmed:
                self._warmed = True
                return
            self._cost_ms = total_ms
            self._settle = SEG_SETTLE_FRAMES
            return

        self._cost_ms = self._cost_ms * 0.9 + total_ms * 0.1
        if self._settle > 0:
            # 주기를 바꾸면 EMA 가 따라오는 데 열 프레임쯤 걸린다. 그 사이에
            # 또 바꾸면 낡은 추정치를 보고 계속 올려버린다.
            self._settle -= 1
            return

        every, seg = self._seg_every, self._seg_cost_ms
        if (
            self._cost_ms > SEG_SLOW_MS
            and seg > SEG_WORTH_SKIPPING_MS
            and every < SEG_EVERY_MAX
        ):
            self._seg_every = every + 1
            self._settle = SEG_SETTLE_FRAMES
        elif every > 1:
            # 한 단계 낮추면 늘어날 비용을 미리 계산해서 판단한다. 고정 임계값만
            # 보면 두 문턱 사이(22~30ms)에 들어온 순간 영영 못 돌아온다.
            predicted = self._cost_ms + seg * (1.0 / (every - 1) - 1.0 / every)
            if predicted < SEG_SLOW_MS:
                self._seg_every = every - 1
                self._settle = SEG_SETTLE_FRAMES

    def reset_mask(self) -> None:
        """마스크와 케이던스 상태만 버린다 (배경 모드 전환 등)."""
        self._prev_mask = None
        self._cached_mask = None
        self._frame_no = 0
        # 케이던스 추정치도 버린다 — 카메라를 바꿨는데 직전 카메라에서
        # 걸린 주기가 그대로 남아 있으면 안 된다.
        self._cost_ms = 0.0
        self._seg_cost_ms = 0.0
        self._seg_every = 1
        self._settle = 0
        self._warmed = False

    def reset_track(self) -> None:
        """프레이밍만 버린다. 추적을 껐다 켤 때만 부른다 — 속도/크기 슬라이더가
        이걸 부르면 드래그할 때마다 화면이 원위치로 튄다."""
        self._track_cur = None
        self._track_goal = None
        self._last_t = None
        self._track_key = None
        self._track_forced = False

    def reset(self) -> None:
        """카메라 교체처럼 전부 버려야 할 때."""
        self.reset_mask()
        self.reset_track()

    # -------------------------------------------------------------- 공통 --

    @staticmethod
    def _downscale(bgr: np.ndarray) -> np.ndarray:
        h, w = bgr.shape[:2]
        if w <= WORK_WIDTH:
            return bgr
        scale = WORK_WIDTH / w
        return cv2.resize(
            bgr, (WORK_WIDTH, max(1, int(round(h * scale)))),
            interpolation=cv2.INTER_AREA,
        )

    @staticmethod
    def _blend(base: np.ndarray, top: np.ndarray, mask3: np.ndarray) -> np.ndarray:
        """base 위에 top 을 mask 만큼 올린다.

        numpy 브로드캐스트 합성은 float64 임시배열이 여러 개 생겨 7.3ms 가 든다.
        같은 결과를 cv2 연산으로 하면 2.4ms.
        """
        diff = cv2.subtract(top, base, dtype=cv2.CV_32F)
        return cv2.add(base, cv2.multiply(diff, mask3), dtype=cv2.CV_8U)

    # ---------------------------------------------------------- 피부 보정 --

    @staticmethod
    def _skin_mask(bgr: np.ndarray) -> np.ndarray:
        """0~1 실수 마스크. 경계를 흐려 보정 자국이 생기지 않게 한다.

        마스크는 원래도 뭉갠 값이라 절반 해상도로 만들어 되키워도 차이가 없다.
        전체 해상도로 계산하면 2.4ms, 절반이면 1.4ms.
        """
        h, w = bgr.shape[:2]
        small = cv2.resize(bgr, (max(1, w // 2), max(1, h // 2)),
                           interpolation=cv2.INTER_AREA)
        ycrcb = cv2.cvtColor(small, cv2.COLOR_BGR2YCrCb)
        mask = cv2.inRange(ycrcb, SKIN_LOW, SKIN_HIGH)
        mask = cv2.medianBlur(mask, 5)  # 점점이 튀는 오검출 제거
        mask = cv2.GaussianBlur(mask, (0, 0), 3)
        mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_LINEAR)
        return mask.astype(np.float32) / 255.0

    @classmethod
    def _smooth_skin(cls, bgr: np.ndarray, strength: int) -> np.ndarray:
        """피부 영역에만 bilateral 필터를 강도만큼 섞는다. 눈/입 윤곽은 살아남는다."""
        amount = max(0.0, min(1.0, strength / 100.0))
        smooth = cv2.bilateralFilter(bgr, 9, 60, 60)
        mask = cls._skin_mask(bgr) * amount
        return cls._blend(bgr, smooth, cv2.merge([mask, mask, mask]))

    @staticmethod
    def _adjust_tone(bgr: np.ndarray, tone: int, bright: int) -> np.ndarray:
        """따뜻하게(R 올리고 B 내림) + 밝기. cv2.add 는 포화 연산이라 클리핑이 공짜다."""
        shift = int(round(tone * 0.30))  # 최대 +-16
        lift = int(round(bright * 0.60))  # 최대 +-27
        return cv2.add(bgr, (lift - shift, lift, lift + shift, 0))

    # ---------------------------------------------------------- 배경 제거 --

    def _make_onnx_infer(self):
        """onnxruntime 백엔드 — 같은 모델을 cv2.dnn 보다 5배 빠르게 돌린다.

        실측 23.1ms -> 4.4ms. 두 백엔드의 출력이 최대 오차 0.000036, 0.5 기준
        일치율 100% 로 사실상 동일함을 확인하고 채택했다.
        """
        try:
            import onnxruntime as ort
        except ImportError:
            return None
        try:
            blob = np.fromfile(asset_path(ONNX_NAME), dtype=np.uint8)
        except OSError:
            return None
        if blob.size == 0:
            return None
        try:
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2  # 캡처/보정 스레드에 코어를 남긴다
            options.log_severity_level = 3
            # 경로가 아니라 bytes 로 넘긴다 — 한글 사용자명 경로 대응
            session = ort.InferenceSession(
                blob.tobytes(), options, providers=["CPUExecutionProvider"]
            )
            name = session.get_inputs()[0].name
        except Exception:
            return None

        def infer(rgb: np.ndarray) -> np.ndarray:
            feed = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None, ...])
            return np.squeeze(session.run(None, {name: feed})[0])

        self.backend = "onnxruntime"
        return infer

    def _make_cv_infer(self):
        r"""cv2.dnn 폴백 — onnxruntime 이 없거나 .onnx 가 빠졌을 때 쓴다.

        경로를 넘기지 않고 **바이트로 읽어서** 넘긴다. OpenCV 의 파일 입출력은
        Windows 에서 경로에 비ASCII 문자가 있으면 실패하는데, exe 로 묶으면
        모델이 %TEMP%\_MEIxxxx 로 풀리고 사용자 이름이 한글이면 그 경로가
        비ASCII 가 되어 배경 제거만 조용히 죽는다.
        """
        try:
            blob = np.fromfile(model_path(), dtype=np.uint8)
            # ENGINE_CLASSIC 이 새 그래프 엔진보다 빠르다 (19.0ms vs 21.5ms 실측).
            # 새 엔진은 setPreferableBackend/Target 도 무시하므로 얻을 게 없다.
            net = cv2.dnn.readNetFromTFLite(blob, cv2.dnn.ENGINE_CLASSIC)
        except (OSError, cv2.error):
            return None

        def infer(rgb: np.ndarray) -> np.ndarray:
            net.setInput(cv2.dnn.blobFromImage(rgb))
            output = net.forward()
            return output[0, 0] if output.shape[1] == 1 else output[0, :, :, 0]

        self.backend = "cv2.dnn"
        return infer

    def _load_net(self):
        """추론 백엔드를 처음 켤 때 한 번만 만든다 (시작 시간 보호)."""
        if self._infer is not None or self._net_failed:
            return self._infer
        self._infer = self._make_onnx_infer() or self._make_cv_infer()
        if self._infer is None:
            self._net_failed = True  # 모델이 없거나 손상 — 배경 기능만 포기
        return self._infer

    @staticmethod
    def _fast_guided_filter(mask: np.ndarray, guide_bgr: np.ndarray,
                            radius: int = 8, eps: float = 1e-3,
                            scale: int = 4) -> np.ndarray:
        """He & Sun 의 Fast Guided Filter — 마스크 경계를 원본 윤곽에 맞춘다.

        핵심은 **계수를 축소 격자에서 구하고, 계수를 업샘플해서 풀해상도에
        적용**하는 것이다 (`q = a·I + b`). 예전에 이걸 "출력을 축소했다가
        되키우는" 식으로 잘못 구현해놓고 "+5% 에 +20ms" 라며 기각했었다.
        제대로 하면 같은 품질이 2.5ms 안에 나온다.

        가이드는 그레이스케일을 쓴다. 컬러 가이드는 픽셀마다 3x3 공분산을
        역행렬해야 해서 알파 정제에 쓰기엔 비싸고, 이득도 크지 않다.
        """
        gray = cv2.cvtColor(guide_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        h, w = gray.shape
        sw, sh = max(8, w // scale), max(8, h // scale)
        r = max(1, radius // scale)

        gs = cv2.resize(gray, (sw, sh), interpolation=cv2.INTER_AREA)
        ms = cv2.resize(mask, (sw, sh), interpolation=cv2.INTER_AREA)

        ksize = (r * 2 + 1, r * 2 + 1)
        mean_g = cv2.boxFilter(gs, cv2.CV_32F, ksize)
        mean_m = cv2.boxFilter(ms, cv2.CV_32F, ksize)
        var_g = cv2.boxFilter(gs * gs, cv2.CV_32F, ksize) - mean_g * mean_g
        cov_gm = cv2.boxFilter(gs * ms, cv2.CV_32F, ksize) - mean_g * mean_m

        a = cov_gm / (var_g + eps)
        b = mean_m - a * mean_g
        a = cv2.boxFilter(a, cv2.CV_32F, ksize)
        b = cv2.boxFilter(b, cv2.CV_32F, ksize)

        # 계수만 키우고 적용은 풀해상도 가이드로 — 여기서 미세 구조가 살아난다
        a = cv2.resize(a, (w, h), interpolation=cv2.INTER_LINEAR)
        b = cv2.resize(b, (w, h), interpolation=cv2.INTER_LINEAR)
        return np.clip(a * gray + b, 0.0, 1.0)

    @staticmethod
    def _contrast(mask: np.ndarray) -> np.ndarray:
        """확률값을 0/1 쪽으로 밀어 인물이 반투명하게 비치는 것을 막는다."""
        return np.clip((mask - MASK_MID) * MASK_GAIN + 0.5, 0, 1).astype(np.float32)

    @staticmethod
    def _drop_specks(mask: np.ndarray) -> np.ndarray:
        """본체에서 떨어져 나온 작은 오검출 덩어리를 지운다.

        가장 큰 덩어리만 남기면 팔이 잘리거나 두 사람 중 하나가 사라지므로,
        가장 큰 것의 BLOB_KEEP_RATIO 이상인 덩어리는 모두 살린다.

        **버릴 덩어리에만 손을 댄다.** 예전에는 *살릴* 마스크를 블러해서 마스크
        전체에 곱했는데, 그러면 인물 자신의 경계(마스크 ~0.5)에서도 blurred keep
        이 ~0.5 라 알파가 0.25 로 깎였다. 게다가 잡티가 하나도 없으면 이 코드가
        통째로 건너뛰어서, 화면에 잡티가 생겼다 사라질 때마다 외곽선 전체의
        알파가 절반이 됐다 두 배가 되는 깜빡임이 생겼다.
        """
        binary = (mask > 0.5).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
        if count <= 2:  # 배경 + 덩어리 하나 이하 — 지울 게 없다
            return mask
        areas = stats[1:, cv2.CC_STAT_AREA]
        threshold = areas.max() * BLOB_KEEP_RATIO
        drop_ids = [i + 1 for i, area in enumerate(areas) if area < threshold]
        if not drop_ids:
            return mask
        # 잡티는 본체와 떨어져 있으므로 하드 컷이어도 이음매가 생기지 않는다.
        out = mask.copy()
        out[np.isin(labels, drop_ids)] = 0.0
        return out

    def _person_mask(self, bgr: np.ndarray) -> np.ndarray | None:
        started = time.perf_counter()
        mask, inferred = self._person_mask_inner(bgr)
        # **추론이 실제로 돈 프레임만** 평균에 넣는다. 건너뛴 프레임의 0ms 까지
        # 섞으면 이 값이 '추론 1회 비용'이 아니라 '분할상환 비용'이 돼버려,
        # _tune_cadence 의 "건너뛸 가치가 있는가" 판단이 무의미해진다.
        if inferred:
            seg_ms = (time.perf_counter() - started) * 1000.0
            self._seg_cost_ms = (
                seg_ms if not self._seg_cost_ms else self._seg_cost_ms * 0.9 + seg_ms * 0.1
            )
        return mask

    def _person_mask_inner(self, bgr: np.ndarray) -> tuple[np.ndarray | None, bool]:
        """(마스크, 이번 프레임에 추론이 실제로 돌았는지)"""
        infer = self._load_net()
        if infer is None:
            return None, False
        h, w = bgr.shape[:2]

        self._frame_no += 1
        # reset() 은 GUI 스레드에서 불린다. 아래 검사와 사용 사이에 끼어들면
        # None 을 참조해 예외가 나고, camera.py 의 except 가 루프 전체를
        # 감싸고 있어서 미리보기가 통째로 죽는다. 지역 변수로 먼저 고정한다.
        cached = self._cached_mask
        if (
            cached is not None
            and cached.shape == (h, w)
            and self._frame_no % self._seg_every
        ):
            return cached, False

        rgb = cv2.cvtColor(
            cv2.resize(bgr, (SEG_INPUT, SEG_INPUT), interpolation=cv2.INTER_LINEAR),
            cv2.COLOR_BGR2RGB,
        ).astype(np.float32) / 255.0
        try:
            person = infer(rgb)  # 이 모델은 0~1 을 그대로 받는다
        except Exception:
            # 추론이 깨져도 캡처 스레드를 죽이지 않는다 — 배경 기능만 포기
            self._net_failed = True
            self._infer = None
            return None, True

        # 시간축 평활은 256x256 단계에서 한다 — 싸고, 다듬은 뒤에 섞어서
        # 선명한 경계가 다시 뭉개지는 것도 피할 수 있다.
        #
        # 고정 가중치는 정지 상태엔 너무 약해 깜빡이고 고개를 돌리면 너무 강해
        # 잔상이 남는다. 픽셀별로 변화량을 보고 가중치를 정하면 둘 다 잡힌다 —
        # 안 움직인 곳은 세게 평활하고, 움직인 곳은 새 값을 그대로 쓴다.
        previous = self._prev_mask  # reset() 이 끼어들 수 있어 먼저 고정한다
        if previous is not None and previous.shape == person.shape:
            motion = cv2.absdiff(previous, person)
            weight = TEMPORAL_MAX * (1.0 - np.clip(motion / TEMPORAL_MOTION, 0.0, 1.0))
            person = previous * weight + person * (1.0 - weight)
        self._prev_mask = person

        # 잡티 제거는 업스케일 전 256x256 에서 — 픽셀이 3.5배 적다.
        person = self._drop_specks(person)
        mask = cv2.resize(person, (w, h), interpolation=cv2.INTER_LINEAR)
        # 가이디드 필터가 **커브보다 먼저** 와야 한다. _contrast 는 소프트 알파
        # 밴드를 의도적으로 없애는데, 그 밴드가 바로 머리카락이다. 경계를 먼저
        # 실제 윤곽에 맞춘 뒤 굳혀야 머리카락이 남는다.
        mask = self._fast_guided_filter(mask, bgr)
        mask = self._contrast(mask)
        self._cached_mask = mask
        return mask, True

    @staticmethod
    def _blur_background(bgr: np.ndarray, strength: int, mask: np.ndarray) -> np.ndarray:
        """배경만 블러한다 (정규화 합성곱).

        1/4 로 줄여서 블러한다 — 원본 해상도에 sigma=25 를 걸면 커널이 151x151
        이라 40ms 가 넘어 30fps 를 깬다.

        **인물을 빼고 블러하는 게 핵심.** 예전에는 인물이 포함된 프레임을 그대로
        블러해서 배경판으로 썼는데, 그러면 인물 색이 바깥으로 번져 마스크 품질과
        무관한 후광이 항상 남았다. 배경 가중치 b = 1-mask 로 정규화하면
        (num/den) 인물 픽셀은 기여도가 0 이 되고 빈자리는 주변 진짜 배경으로 메워진다.
        """
        h, w = bgr.shape[:2]
        sw, sh = max(1, w // 4), max(1, h // 4)
        sigma = max(1.0, strength / 4.0)

        small = cv2.resize(bgr, (sw, sh), interpolation=cv2.INTER_AREA).astype(np.float32)
        weight = cv2.resize(1.0 - mask, (sw, sh), interpolation=cv2.INTER_AREA)

        num = cv2.GaussianBlur(small * weight[:, :, None], (0, 0), sigma)
        den = cv2.GaussianBlur(weight, (0, 0), sigma)
        blurred = num / np.maximum(den, 1e-3)[:, :, None]

        out = cv2.resize(blurred, (w, h), interpolation=cv2.INTER_LINEAR)
        return np.clip(out, 0, 255).astype(np.uint8)

    def _apply_background(self, bgr: np.ndarray, mask: np.ndarray | None) -> np.ndarray:
        if mask is None:
            return bgr  # 모델이 없으면 조용히 원본 유지

        mode = self.s.bg_mode
        if mode == BG_TRANSPARENT:
            alpha = np.clip(mask * 255.0, 0, 255).astype(np.uint8)
            return cv2.merge([bgr[:, :, 0], bgr[:, :, 1], bgr[:, :, 2], alpha])

        if mode == BG_BLUR:
            background = self._blur_background(bgr, self.s.bg_blur, mask)
        elif mode == BG_COLOR:
            color = self.s.bg_color.lstrip("#")
            r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
            background = np.empty_like(bgr)
            background[:] = (b, g, r)
        elif mode == BG_IMAGE:
            background = self._background_image(bgr.shape[:2])
            if background is None:
                return bgr  # 이미지를 못 읽었다 — 원본을 그대로 둔다
        else:
            return bgr

        mask3 = cv2.merge([mask, mask, mask])
        return self._blend(background, bgr, mask3)

    def _background_image(self, shape: tuple[int, int]) -> np.ndarray | None:
        """배경 이미지를 작업 해상도에 맞춰 돌려준다. 캐시가 핵심이다.

        캐시가 없으면 매 프레임 파일을 디코딩하고 리사이즈한다 — 1080p JPEG
        하나가 30fps 에서 초당 30번 풀린다. 키에 크기를 넣어야 창 크기를 바꿨을
        때 다시 만든다.
        """
        h, w = shape
        key = (self.s.bg_image, w, h)
        if key == self._bg_image_key:
            return self._bg_image

        value = self.s.bg_image or ""
        if backgrounds.is_builtin(value):
            image = backgrounds.render(value, w, h)
        else:
            image = backgrounds.load_file(value, w, h)
            if image is None:
                # 파일이 사라졌거나 이미지가 아니다. 검은 화면을 보여주느니
                # 기본 배경으로 대체한다 — 방송 중에 화면이 죽으면 안 된다.
                image = backgrounds.render(backgrounds.DEFAULT, w, h)

        self._bg_image_key = key
        self._bg_image = image
        return image

    # ------------------------------------------------------------ 인물 추적 --

    def _track_target(self, mask: np.ndarray, shape: tuple[int, int]):
        """마스크에서 인물을 담을 크롭 사각형(중심x, 중심y, 폭, 높이)을 구한다."""
        h, w = shape
        binary = (mask > 0.5).astype(np.uint8)
        bx, by, bw, bh = cv2.boundingRect(binary)
        if bw < 16 or bh < 16:
            return None  # 인물이 없거나 너무 작다 — 직전 프레이밍을 유지한다

        aspect = self.s.w / max(1, self.s.h)  # 창 비율에 맞춰 잘라야 한다
        crop_h = min(float(h), bh * track_zoom(self.s.track_size))
        crop_w = crop_h * aspect
        if crop_w > w:  # 가로가 모자라면 가로에 맞춘다
            crop_w = float(w)
            crop_h = crop_w / aspect
        # 얼굴이 정중앙보다 살짝 위에 오도록 — 방송에서 쓰는 일반적인 프레이밍
        return (bx + bw / 2.0, by + bh * TRACK_HEAD_BIAS, crop_w, crop_h)

    def _apply_tracking(self, frame: np.ndarray, mask: np.ndarray | None) -> np.ndarray:
        """인물을 따라가도록 프레임을 잘라낸다 (오토 프레이밍).

        매 프레임 목표를 그대로 따라가면 마스크가 미세하게 떨릴 때마다 화면이
        흔들린다. 그래서 (1) 목표가 데드존을 벗어날 때만 갱신하고 (2) 현재
        프레이밍을 목표 쪽으로 천천히 이징한다. 크기는 위치보다 더 느리게
        따라가게 해서 줌이 들썩이지 않도록 했다.

        이징은 **경과 시간 기반**이다. 예전처럼 프레임당 고정 비율로 섞으면
        30fps 웹캠과 60fps 웹캠에서 따라오는 속도가 정확히 2배 차이나서,
        속도 슬라이더의 눈금이 카메라마다 다른 뜻이 돼버린다.
        """
        h, w = frame.shape[:2]
        target = self._track_target(mask, (h, w)) if mask is not None else None

        now = time.perf_counter()
        if self._last_t is None:
            dt = 1.0 / 30.0
        else:
            dt = min(TRACK_DT_MAX, max(TRACK_DT_MIN, now - self._last_t))
        self._last_t = now

        pos_dz, size_dz = track_deadzone(self.s.track_speed)

        # 크기 슬라이더를 옮기거나 창 비율을 바꾼 건 **명령**이지 노이즈가
        # 아니다. 데드존은 마스크가 미세하게 떨릴 때 화면이 들썩이는 걸 막으려고
        # 있는 것이지 사용자의 조작을 무시하라고 있는 게 아니다.
        #
        # 이걸 안 하면 크기 조절이 통째로 죽는다. 속도 6 의 크기 데드존은
        # 16.4% 인데 크기 눈금 한 칸은 6~11% 밖에 안 움직이므로, 한 칸씩
        # 옮기는 한 목표가 영원히 갱신되지 않는다. 여러 칸을 한 번에 옮겨도
        # 갱신된 목표가 다시 데드존 안에 들어가 중간에서 멈춰 선다.
        key = (self.s.track_size, round(self.s.w / max(1, self.s.h), 4))
        if key != self._track_key:
            self._track_key = key
            self._track_forced = True  # 인물을 찾은 프레임에서 반영될 때까지 유지

        if self._track_cur is None:
            if target is None:
                return frame  # 아직 인물을 한 번도 못 찾았다
            self._track_cur = self._track_goal = target  # 첫 프레임은 이징 없이 스냅
        elif target is not None:
            cur = self._track_cur
            moved = (
                abs(target[0] - cur[0]) / w > pos_dz
                or abs(target[1] - cur[1]) / h > pos_dz
                or abs(target[3] - cur[3]) / max(1.0, cur[3]) > size_dz
            )
            if moved or self._track_forced:
                self._track_goal = target
                self._track_forced = False

        rate = track_rate(self.s.track_speed)
        a_pos = 1.0 - math.exp(-rate * dt)
        a_size = 1.0 - math.exp(-rate * TRACK_SIZE_RATE_K * dt)

        cur, goal = self._track_cur, self._track_goal
        self._track_cur = (
            cur[0] + (goal[0] - cur[0]) * a_pos,
            cur[1] + (goal[1] - cur[1]) * a_pos,
            cur[2] + (goal[2] - cur[2]) * a_size,
            cur[3] + (goal[3] - cur[3]) * a_size,
        )

        cx, cy, cw, ch = self._track_cur
        cw = max(32.0, min(float(w), cw))
        ch = max(32.0, min(float(h), ch))
        x0 = int(round(min(max(cx - cw / 2.0, 0.0), w - cw)))
        y0 = int(round(min(max(cy - ch / 2.0, 0.0), h - ch)))
        x1 = min(w, x0 + int(round(cw)))
        y1 = min(h, y0 + int(round(ch)))
        if x1 - x0 < 32 or y1 - y0 < 32:
            return frame
        return frame[y0:y1, x0:x1]
