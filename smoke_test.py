"""빠른 자체 점검 — 창을 띄우지 않고(offscreen) 핵심 계산과 조립을 확인한다.

    python smoke_test.py
"""

import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, QTimer
from PySide6.QtGui import QImage, QPixmap, QPainter, QColor
from PySide6.QtWidgets import QApplication, QMenu, QWidget

import settings, camera, winapi, overlay, menu, tray, main

app = QApplication(sys.argv)
# 실제 앱과 같은 스타일을 입힌다. QSS 를 안 걸면 min-height·:focus 같은
# 규칙이 빠진 채로 재서 크기·포커스 검사가 통과해도 의미가 없다.
import theme as _theme
_theme.apply_theme(app)

# 1) cover 계산: 16:9 원본을 정사각 타깃에 -> 좌우가 잘리고 높이는 그대로
img = QImage(1280, 720, QImage.Format.Format_RGB888)
img.fill(QColor("red"))
r = overlay.OverlayWindow._cover_rect(img, QRectF(0, 0, 200, 200))
assert (round(r.width()), round(r.height())) == (720, 720), r
assert round(r.x()) == 280 and round(r.y()) == 0, r

# 2) 세로로 긴 타깃 -> 좌우가 더 잘린다 (원본 높이는 그대로)
r2 = overlay.OverlayWindow._cover_rect(img, QRectF(0, 0, 100, 300))
assert (round(r2.x()), round(r2.width()), round(r2.height())) == (520, 240, 720), r2

# 2b) 세로로 긴 원본을 가로로 긴 타깃에 -> 위아래가 잘린다
tall = QImage(480, 640, QImage.Format.Format_RGB888)
r3 = overlay.OverlayWindow._cover_rect(tall, QRectF(0, 0, 320, 160))
assert (round(r3.y()), round(r3.width()), round(r3.height())) == (200, 480, 240), r3

# 3) 설정 왕복
s = settings.Settings()
s.w, s.h, s.shape = 400, 300, settings.SHAPE_RECT
import json, dataclasses
raw = json.loads(json.dumps(dataclasses.asdict(s)))
s2 = settings.Settings(**raw)
assert s2 == s

# 4) sanitize 가 이상값을 정리하는지
bad = settings.Settings(w=5, h=9999, opacity=500, border_width=-3, shape="blob")
bad.sanitize()
assert (bad.w, bad.opacity, bad.border_width, bad.shape) == (
    settings.MIN_W, 100, 0, settings.SHAPE_ELLIPSE), bad

# 5) 컨트롤러/창/메뉴/트레이 아이콘 생성
#
# 설정 경로를 임시 파일로 돌려놓고 만든다. 안 그러면 (a) 사용자의 실제
# 설정을 읽어와 아래 리사이즈 테스트가 그 사람의 비율 설정에 따라 깨지고,
# (b) 종료 시 aboutToQuit 에서 실제 설정 파일을 덮어쓴다.
_real_settings_path = settings.SETTINGS_PATH
settings.SETTINGS_PATH = os.path.join(os.getcwd(), "_smoke_settings.json")
ctl = main.AppController(app)
ctl.settings.aspect = "free"  # 리사이즈 테스트는 자유 비율을 전제로 한다
assert ctl.window is not None
m = menu.build_menu(ctl)
labels = [a.text() for a in m.actions()]
assert "종료" in labels, labels
icon = tray.make_icon()
assert not icon.isNull()

# 6) paintEvent 가 예외 없이 돌아가는지 (프레임 있음/없음, 원형/사각, 반전 on/off)
w = ctl.window
w.resize(320, 240)
for shape in (settings.SHAPE_ELLIPSE, settings.SHAPE_RECT):
    for mirror in (True, False):
        for frame in (img, None):
            w.s.shape, w.s.mirror, w._frame = shape, mirror, frame
            w._status = "" if frame is not None else "test"
            pm = QPixmap(w.size())
            pm.fill(QColor(0, 0, 0, 0))
            w.render(pm)

# 7) 리사이즈 계산 (오른쪽 아래로 50,50 드래그)
from PySide6.QtCore import QPoint, QRect
w.setGeometry(100, 100, 200, 150)
w._resize_edge = overlay._RIGHT | overlay._BOTTOM
w._resize_start_geo = QRect(100, 100, 200, 150)
w._resize_start_pos = QPoint(300, 250)
w._do_resize(QPoint(350, 300), False)
assert w.geometry().size().toTuple() == (250, 200), w.geometry()

# 왼쪽 위 모서리를 잡고 끌면 오른쪽 아래가 고정되어야 한다
w.setGeometry(100, 100, 200, 150)
w._resize_start_geo = QRect(100, 100, 200, 150)
w._resize_edge = overlay._LEFT | overlay._TOP
w._resize_start_pos = QPoint(100, 100)
w._do_resize(QPoint(60, 70), False)
g = w.geometry()
assert (g.right(), g.bottom()) == (299, 249), g

# 7b) 비율 고정: 8개 비율 x 4개 모서리 — 비율이 유지되고 최소 크기를 안 깬다
import numpy as np
for key, _label, ratio in settings.ASPECTS:
    if ratio is None:
        continue
    w.s.aspect = key
    for edge in (overlay._RIGHT | overlay._BOTTOM, overlay._LEFT | overlay._TOP,
                 overlay._RIGHT, overlay._BOTTOM, overlay._LEFT | overlay._BOTTOM):
        for drag in ((80, 20), (-90, -70), (5, 120), (-140, -160)):
            w.setGeometry(400, 400, 200, 150)
            w._resize_start_geo = QRect(400, 400, 200, 150)
            w._resize_edge = edge
            w._resize_start_pos = QPoint(500, 500)
            w._do_resize(QPoint(500 + drag[0], 500 + drag[1]), False)
            g = w.geometry()
            assert g.width() >= settings.MIN_W and g.height() >= settings.MIN_H, (key, g)
            got = g.width() / g.height()
            assert abs(got - ratio) / ratio < 0.02, (key, edge, drag, g, got, ratio)

# 휠 확대도 비율을 유지한다
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QWheelEvent
w.s.aspect = "16:9"
w.setGeometry(100, 100, 320, 180)
w.wheelEvent(QWheelEvent(QPointF(50, 50), QPointF(150, 150), QPoint(0, 0),
                         QPoint(0, 120), Qt.MouseButton.NoButton,
                         Qt.KeyboardModifier.NoModifier,
                         Qt.ScrollPhase.NoScrollPhase, False))
g = w.geometry()
assert g.width() > 320 and abs(g.width() / g.height() - 16 / 9) < 0.02, g

# 7c) 자유 변형 회귀 — Shift 없이 끌면 가로세로가 따로 움직여야 한다
w.s.aspect = "free"
w.s.lock_aspect = False
w.setGeometry(100, 100, 200, 150)
w._resize_start_geo = QRect(100, 100, 200, 150)
w._resize_edge = overlay._RIGHT | overlay._BOTTOM
w._resize_start_pos = QPoint(300, 250)
w._do_resize(QPoint(400, 260), False)
assert w.geometry().size().toTuple() == (300, 160), w.geometry()

# 자유 + Shift 는 현재 비율 유지
w.setGeometry(100, 100, 200, 150)
w._resize_start_geo = QRect(100, 100, 200, 150)
w._resize_start_pos = QPoint(300, 250)
w._do_resize(QPoint(400, 260), True)
g = w.geometry()
assert abs(g.width() / g.height() - 200 / 150) < 0.02, g

# 7d) 크기 프리셋이 비율마다 다른 픽셀을 낸다
assert settings.size_for("16:9", 320, 320, 240) == (320, 180)
assert settings.size_for("9:16", 320, 320, 240) == (180, 320)
assert settings.size_for("1:1", 320, 320, 240) == (320, 320)
# 자유 모드는 현재 창 비율(2:1)을 유지한 채 긴 변만 맞춘다
assert settings.size_for("free", 400, 300, 150) == (400, 200)
# 작은 프리셋이라도 최소 크기 아래로는 안 내려간다
for key, _l, r in settings.ASPECTS:
    if r is None:
        continue
    pw, ph = settings.size_for(key, 60, 320, 240)
    assert pw >= settings.MIN_W and ph >= settings.MIN_H, (key, pw, ph)

# 7e) 구버전 설정(square) 마이그레이션
import io, json
tmp = os.path.join(os.getcwd(), "_migrate.json")
io.open(tmp, "w", encoding="utf-8").write(
    json.dumps({"square": True, "w": 300, "h": 200}))
_orig_path = settings.SETTINGS_PATH
settings.SETTINGS_PATH = tmp
migrated = settings.Settings.load()
settings.SETTINGS_PATH = _orig_path
os.remove(tmp)
assert migrated.aspect == "1:1", migrated.aspect
assert migrated.w == migrated.h, (migrated.w, migrated.h)

# 7f) 피부 보정 파이프라인
import effects
s3 = settings.Settings()
proc = effects.FrameProcessor(s3)
frame = np.full((720, 1280, 3), 120, dtype=np.uint8)
frame[:, :, 1] = 150; frame[:, :, 2] = 190          # 살색에 가깝게
assert not proc.enabled()
assert proc.process(frame) is frame                 # 꺼져 있으면 원본 그대로

s3.skin_smooth, s3.skin_tone, s3.skin_bright = 60, 30, 25
assert proc.enabled()
out = proc.process(frame)
# 처리 해상도는 캡처가 아니라 **창**이 정한다. 320x240 창에 1280x720 을 그대로
# 통과시키면 보여 줄 수 없는 픽셀에 프레임 예산을 다 쓰게 되고, 그러면 예산을
# 넘겨 낡은 마스크를 재사용하게 된다 — 그게 움직일 때의 잔상이었다.
assert out.shape[1] < 1280, ("창보다 큰 해상도로 계산했다", out.shape)
assert out.shape[1] >= s3.w, ("창을 채울 픽셀도 없다", out.shape, s3.w)
assert out.dtype == np.uint8 and out.ndim == 3
# 창을 키우면 해상도가 따라 올라가고, 원본보다 크게 확대하지는 않는다.
s3.w, s3.h = 1400, 1050
assert proc.process(frame).shape[1] > out.shape[1], "창을 키웠는데 안 따라온다"
assert proc.process(frame).shape[1] <= 1280, "원본보다 크게 확대했다"
s3.w, s3.h = 320, 240

warm = effects.FrameProcessor._adjust_tone(np.full((40, 40, 3), 100, np.uint8), 50, 0)
assert warm[0, 0, 2] > 100 and warm[0, 0, 0] < 100, warm[0, 0]
bright = effects.FrameProcessor._adjust_tone(np.full((4, 4, 3), 250, np.uint8), 50, 50)
assert bright.max() == 255, bright.max()

# 7g) 배경 제거 — 모델이 있으면 4가지 모드가 올바른 모양을 낸다
s3.skin_smooth = s3.skin_tone = s3.skin_bright = 0
if effects.model_available():
    for mode, want_ch in ((settings.BG_TRANSPARENT, 4),
                          (settings.BG_BLUR, 3),
                          (settings.BG_COLOR, 3)):
        s3.bg_mode = mode
        proc.reset()
        res = proc.process(frame)
        assert res.shape[2] == want_ch, (mode, res.shape)
        # 크기는 창에 맞춘 처리 해상도를 그대로 유지해야 한다 (배경 처리가
        # 몰래 더 줄이면 안 된다).
        work = proc._downscale(frame, proc._work_width(frame.shape[1], frame.shape[0]))
        assert res.shape[:2] == work.shape[:2], (mode, res.shape, work.shape)
        if mode == settings.BG_TRANSPARENT:
            assert np.array_equal(res[:, :, :3], work), "투명 배경에서도 색은 손대지 않는다"
        assert res.dtype == np.uint8
    print("bg modes OK (model present)")
else:
    # 모델이 없으면 배경 기능만 조용히 포기하고 원본을 유지해야 한다
    s3.bg_mode = settings.BG_TRANSPARENT
    proc.reset()
    assert proc.process(frame).shape[2] == 3
    print("bg gracefully disabled (no model)")
s3.bg_mode = settings.BG_OFF

# 7g2) 배경 이미지
import cv2

import backgrounds

for _key, _label in backgrounds.BUILTINS:
    _img = backgrounds.render(_key, 160, 90)
    assert _img.shape == (90, 160, 3) and _img.dtype == np.uint8, (_key, _img.shape)
    # 같은 크기면 늘 같은 그림이어야 한다 (난수 시드 고정) — 프레임마다
    # 배경이 달라지면 못 쓴다
    assert np.array_equal(_img, backgrounds.render(_key, 160, 90)), _key
    # 단색 판이면 배경 구실을 못 한다
    assert int(_img.max()) - int(_img.min()) > 24, ("%s 가 밋밋하다" % _key)
# 어떤 크기를 줘도 그 크기로 나온다
assert backgrounds.render("studio", 7, 3).shape == (3, 7, 3)
assert backgrounds.render("없는키", 8, 8).shape == (8, 8, 3)  # 모르는 키는 기본값

# cover: 비율이 달라도 꽉 채우고 가운데를 남긴다.
# 크기만 확인하면 안 된다 — 그냥 resize 해도 크기는 맞는다. 원이 원으로 남는지
# 봐야 눌러 찌그러뜨린 건지 잘라낸 건지 구분된다.
_wide = np.zeros((100, 400, 3), np.uint8)
cv2.circle(_wide, (200, 50), 40, (255, 255, 255), -1)
assert backgrounds.cover(_wide, 90, 160).shape == (160, 90, 3)
_sq = backgrounds.cover(_wide, 100, 100)
assert _sq.shape == (100, 100, 3), _sq.shape
_ys, _xs = np.where(_sq[:, :, 0] > 128)
_cw, _ch = _xs.max() - _xs.min(), _ys.max() - _ys.min()
assert abs(_cw - _ch) <= 2, ("cover 가 비율을 안 지켰다 (눌러 찌그러뜨렸다)", _cw, _ch)
assert backgrounds.cover(_wide, 400, 100).shape == (100, 400, 3)

# 사용자 이미지 — **한글 경로**로 왕복한다. cv2.imread 를 쓰면 여기서 죽는다.
import tempfile as _tf
_dir = _tf.mkdtemp()
_kdir = os.path.join(_dir, "한글폴더")
os.makedirs(_kdir, exist_ok=True)
_kpath = os.path.join(_kdir, "배경사진.png")
_src = np.zeros((120, 200, 3), np.uint8)
_src[:, :100] = (0, 0, 255)
cv2.imencode(".png", _src)[1].tofile(_kpath)  # imwrite 도 한글 경로에서 실패한다
_loaded = backgrounds.load_file(_kpath, 64, 64)
assert _loaded is not None, "한글 경로 이미지를 못 읽었다"
assert _loaded.shape == (64, 64, 3), _loaded.shape
assert backgrounds.load_file(os.path.join(_kdir, "없음.png"), 8, 8) is None
assert backgrounds.load_file(_kpath + ".txt", 8, 8) is None

# sanitize 가 사라진 파일을 기본 배경으로 되돌리는지
_bs = settings.Settings()
_bs.bg_image = _kpath
_bs.sanitize()
assert _bs.bg_image == _kpath, _bs.bg_image
_bs.bg_image = os.path.join(_kdir, "사라진파일.png")
_bs.sanitize()
assert _bs.bg_image == "builtin:studio", _bs.bg_image

if effects.model_available():
    _is = settings.Settings()
    _is.bg_mode = settings.BG_IMAGE
    _is.bg_image = "builtin:blue"
    _ip = effects.FrameProcessor(_is)
    _frame = np.full((360, 640, 3), 90, np.uint8)
    _out = _ip.process(_frame)
    assert _out.shape[2] == 3 and _out.dtype == np.uint8, _out.shape
    # 배경 이미지 캐시가 실제로 재사용되는지 (매 프레임 디코딩하면 안 된다)
    _first = _ip._bg_image
    _ip.process(_frame)
    assert _ip._bg_image is _first, "배경 이미지를 프레임마다 다시 만든다"
    # 배경을 바꾸면 캐시가 갱신돼야 한다
    _is.bg_image = "builtin:sunset"
    _ip.process(_frame)
    assert _ip._bg_image is not _first, "배경을 바꿨는데 캐시가 그대로다"
    # 깨진 경로여도 죽지 않고 기본 배경으로 버틴다
    _is.bg_image = os.path.join(_kdir, "없는파일.png")
    assert _ip.process(_frame).shape[2] == 3

# 7h) 케이던스: 모델 로딩 프레임(첫 프레임 100ms+)이 주기를 튀게 하면 안 된다.
#     이게 없어서 실행할 때마다 20프레임 넘게 1/3 로만 추론하던 버그를 놓쳤다.
s4 = settings.Settings(); s4.bg_mode = settings.BG_COLOR; s4.camera_fps = 30
p4 = effects.FrameProcessor(s4)
p4._seg_cost_ms = 6.0
p4._tune_cadence(117.0)                     # 모델 로딩이 섞인 첫 프레임
for _ in range(60):
    p4._tune_cadence(18.0)                  # 예산에 한참 못 미치는 정상 프레임
assert p4._work_scale == 1.0, ("로딩 프레임에 해상도가 튀었다", p4._work_scale)
assert p4._seg_every == 1, ("낡은 마스크를 쓰기 시작했다", p4._seg_every)

# 데드밴드(22~30ms)에 갇히지 않고 돌아오는지
s5 = settings.Settings(); s5.camera_fps = 30
p5 = effects.FrameProcessor(s5)
p5._seg_cost_ms = 6.0
p5._tune_cadence(117.0)
for i in range(300):
    p5._tune_cadence(24.0 + (6.0 if i % p5._seg_every == 0 else 0.0))
assert p5._work_scale == 1.0, ("데드밴드에 갇혔다", p5._work_scale)

# 같은 18ms 처리는 60fps의 16.7ms 예산에는 느리다. 이때 물러서는 곳은 추론
# 주기가 아니라 **처리 해상도**여야 한다. 주기를 늘리면 낡은 마스크가 새 영상에
# 씌워져 움직일 때 잔상이 남는다 — 해상도를 줄이면 조금 부드러워질 뿐이다.
s60 = settings.Settings(); s60.camera_fps = 60
p60 = effects.FrameProcessor(s60); p60._seg_cost_ms = 6.0
p60._tune_cadence(117.0)
for _ in range(60):
    p60._tune_cadence(18.0)
assert p60._work_scale < 1.0, ("60fps 예산에 맞춰 적응하지 않았다", p60._work_scale)
assert p60._seg_every == 1, ("해상도 대신 마스크를 낡혔다", p60._seg_every)

# 물러설 곳이 없어도(해상도 바닥) 마스크는 절대 낡히지 않는다.
sfl = settings.Settings(); sfl.camera_fps = 60
pfl = effects.FrameProcessor(sfl); pfl._seg_cost_ms = 6.0
pfl._tune_cadence(117.0)
for _ in range(400):
    pfl._tune_cadence(200.0)                # 어떤 해상도로도 못 맞추는 부하
assert pfl._work_scale == effects.WORK_SCALE_MIN, ("바닥까지 안 내려갔다", pfl._work_scale)
assert pfl._seg_every == 1, ("과부하에서 마스크를 낡혔다", pfl._seg_every)

# 처리 해상도는 캡처가 아니라 창 크기가 정한다 — 494 창에 1920 을 통과시키면
# 보여 줄 수 없는 픽셀에 프레임 예산을 다 쓰고, 그게 잔상의 출발점이었다.
sw = settings.Settings(); sw.w = sw.h = 494
pw = effects.FrameProcessor(sw)
assert pw._work_width(1920, 1440) < 1920, "창보다 큰 해상도로 계산하고 있다"
assert pw._work_width(640, 480) == 640, "원본보다 크게 확대하면 안 된다"
sw.w = sw.h = 1400
assert pw._work_width(1920, 1440) > 988, "창을 키웠는데 해상도가 안 따라온다"

# 고배율(HiDPI) 화면: 논리 크기가 아니라 실제로 칠해지는 물리 픽셀을 따라야
# 한다. 250% 화면에서 494 논리픽셀 창은 1235 물리픽셀로 그려진다.
sd = settings.Settings(); sd.w = sd.h = 494
pd = effects.FrameProcessor(sd)
logical_only = pd._work_width(1920, 1440)
sd.view_size = (1235, 1235)                      # dpr 2.5
assert pd._work_width(1920, 1440) > logical_only, "물리 픽셀을 무시하고 있다"
assert pd._work_width(1920, 1440) >= 1235, "칠해지는 픽셀보다 낮은 해상도로 계산한다"

# 7i) 인물 추적
s6 = settings.Settings(); s6.track_enabled = True; s6.track_size = 5; s6.w = s6.h = 360
p6 = effects.FrameProcessor(s6)
assert p6.enabled(), "추적만 켜도 파이프라인이 돌아야 한다"
big = np.zeros((360, 640, 3), np.uint8)
big[:, :, :] = 40
if effects.model_available():
    out6 = p6.process(big)
    # 창 비율(1:1)에 맞춰 잘려 나오거나, 인물이 없으면 원본을 유지한다
    assert out6.ndim == 3 and out6.dtype == np.uint8, out6.shape
    # 데드존: 같은 프레임을 반복해도 크롭이 흔들리지 않아야 한다
    for _ in range(8):
        p6.process(big)
    first = p6._track_cur
    for _ in range(8):
        p6.process(big)
    if first is not None and p6._track_cur is not None:
        drift = abs(first[0] - p6._track_cur[0])
        assert drift < 2.0, ("정지 화면에서 크롭이 흔들린다", drift)
s6.track_enabled = False
p7 = effects.FrameProcessor(s6)
assert p7.process(big) is big, "추적을 끄면 원본이 그대로 나와야 한다"

# 7j) 추적 매핑 — 설계 표와 대조
for v, tau, zoom, dz in ((1, 1.200, 2.600, 0.160),
                         (6, 0.267, 1.578, 0.063),
                         (10, 0.080, 1.200, 0.030)):
    assert abs(1/settings.track_rate(v) - tau) < 0.002, (v, 1/settings.track_rate(v))
    assert abs(settings.track_zoom(v) - zoom) < 0.002, (v, settings.track_zoom(v))
    assert abs(settings.track_deadzone(v)[0] - dz) < 0.002, (v, settings.track_deadzone(v))
# 단조성: 속도가 오르면 빨라지고 둔감함이 줄어든다 / 크기가 오르면 인물이 커진다
rates = [settings.track_rate(v) for v in range(1, 11)]
zooms = [settings.track_zoom(v) for v in range(1, 11)]
assert rates == sorted(rates), rates
assert zooms == sorted(zooms, reverse=True), zooms

# 7k) 구버전 track 문자열 마이그레이션
tmp2 = os.path.join(os.getcwd(), "_track_migrate.json")
_saved = settings.SETTINGS_PATH
for old_val, want_size, want_on in (("off", 5, False), ("wide", 2, True),
                                    ("normal", 5, True), ("close", 8, True)):
    io.open(tmp2, "w", encoding="utf-8").write(json.dumps({"track": old_val}))
    settings.SETTINGS_PATH = tmp2
    got = settings.Settings.load()
    settings.SETTINGS_PATH = _saved
    assert got.track_enabled is want_on, (old_val, got.track_enabled)
    assert got.track_size == want_size, (old_val, got.track_size)
    assert got.track_speed == 6, (old_val, got.track_speed)
os.remove(tmp2)

# 7l) 이징이 프레임레이트에 안 휘둘리는지 — 같은 시간을 30fps 와 60fps 로 나눠
#     흘려보내면 도달 지점이 같아야 한다. 이게 예전 버그의 회귀 테스트다.
import math as _math
def _settle(fps, seconds, rate):
    cur, goal, dt = 0.0, 1.0, 1.0 / fps
    for _ in range(int(seconds * fps)):
        cur += (goal - cur) * (1.0 - _math.exp(-rate * dt))
    return cur
r = settings.track_rate(6)
a30, a60 = _settle(30, 1.0, r), _settle(60, 1.0, r)
assert abs(a30 - a60) < 0.005, (a30, a60)

# 7m) 속도/크기 슬라이더가 프레이밍 상태를 날리지 않는지
if effects.model_available():
    s8 = settings.Settings(); s8.track_enabled = True; s8.w = s8.h = 360
    p8 = effects.FrameProcessor(s8)
    p8.process(big); p8.process(big)
    p8._track_cur = (100.0, 100.0, 200.0, 200.0)  # 프레이밍이 잡힌 상태를 가정
    for v in range(1, 11):
        s8.track_speed = v
        s8.track_size = v
    assert p8._track_cur is not None, "슬라이더가 프레이밍을 날렸다"

# 7m2) 크기 슬라이더가 실제로 크롭 크기를 바꾸는지.
#
# 데드존은 마스크가 떨릴 때 화면이 들썩이는 걸 막으려고 있다. 그런데 그게
# 사용자의 조작까지 막고 있었다 — 속도 6 의 크기 데드존은 16.4% 인데 크기
# 눈금 한 칸은 6~11% 라서, 한 칸씩 옮기는 한 목표가 영원히 갱신되지 않았다.
# 크기 조절이 통째로 죽어 있었고 아무 테스트도 이걸 못 봤다.
_ts = settings.Settings()
_ts.track_enabled = True
_ts.bg_mode = settings.BG_OFF
_ts.w, _ts.h = 320, 180
_tp = effects.FrameProcessor(_ts)
_TH, _TW = 360, 640
_tf = np.zeros((_TH, _TW, 3), np.uint8)
_tm = np.zeros((_TH, _TW), np.float32)
_tm[80:300, 260:380] = 1.0  # 인물 상자: 세로 220

def _settle(n=900):
    for _ in range(n):
        _out = _tp._apply_tracking(_tf, _tm)
    return _out.shape[0]

_ts.track_size = 5
_tp.reset()
_prev = _settle()
# 한 칸씩 올리면 매번 더 크게(=크롭은 더 좁게) 나와야 한다
for _v in (6, 7, 8, 9, 10):
    _ts.track_size = _v
    _got = _settle()
    _want = min(_TH, 220 * settings.track_zoom(_v))
    assert abs(_got - _want) <= 4, ("크기 %d 크롭 불일치" % _v, _got, round(_want, 1))
    assert _got < _prev, ("크기를 올렸는데 크롭이 안 좁아졌다", _v, _prev, _got)
    _prev = _got
# 되돌리면 다시 넓어져야 한다
_ts.track_size = 1
assert _settle() > _prev, "크기를 내렸는데 크롭이 안 넓어졌다"

# 창 비율을 바꿔도 프레이밍이 따라와야 한다 (같은 데드존에 걸려 있었다)
_ts.track_size = 6
_settle()
_before_w = _tp._apply_tracking(_tf, _tm).shape[1]
_ts.w, _ts.h = 180, 320  # 세로형으로
_after_w = _settle()
assert _tp._apply_tracking(_tf, _tm).shape[1] != _before_w, (
    "창 비율을 바꿨는데 크롭 가로가 그대로다")

# 7n) 설정 패널 — 이번 산출물의 최대 신규 모듈이라 자동 검증이 필요하다
from PySide6.QtCore import QEvent, QPointF
from PySide6.QtGui import QMouseEvent
from panel import JumpSlider, SettingsPanel

ctl.open_panel()
pnl = ctl._panel
assert pnl is not None and pnl.isVisible()

# 밖에서 값을 바꾸면 패널이 따라온다 (sync_panel 배선 회귀 방지)
ctl.set_opacity(50)
assert pnl.opacity.slider.value() == 50, pnl.opacity.slider.value()
ctl.set_skin_smooth(85)
assert pnl.smooth.slider.value() == 85, pnl.smooth.slider.value()
ctl.set_track_enabled(True)
assert pnl.track_toggle.isChecked()
assert pnl.speed.slider.isEnabled(), "추적을 켜면 속도 슬라이더가 살아야 한다"

# 패널 슬라이더 -> 설정
pnl.speed.slider.setValue(9)
assert ctl.settings.track_speed == 9, ctl.settings.track_speed
pnl.size_row.slider.setValue(3)
assert ctl.settings.track_size == 3, ctl.settings.track_size

# 회색 처리: 추적을 끄면 속도/크기가 죽는다
ctl.set_track_enabled(False)
assert not pnl.speed.slider.isEnabled()

# 창을 숨기면 패널도 숨고, 다시 보이면 복원된다
ctl.set_hidden(True)
assert not pnl.isVisible(), "숨기기 단축키가 패널을 남겼다"
ctl.set_hidden(False)
assert pnl.isVisible(), "다시 보일 때 패널이 복원되지 않았다"

# 가운데 버튼은 바깥클릭 필터가 삼키면 안 된다 (삼키면 토글이 즉시 재오픈된다)
_far = QPointF(9000, 9000)
pnl.eventFilter(ctl.window, QMouseEvent(
    QEvent.Type.MouseButtonPress, _far, _far, Qt.MouseButton.MiddleButton,
    Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier))
assert pnl.isVisible(), "가운데 버튼을 필터가 삼켰다"

# JumpSlider: 홈 어디를 눌러도 드래그가 시작된다
_js = JumpSlider(); _js.setRange(1, 10); _js.resize(276, 24)
_missed = 0
for _x in range(0, 276, 3):
    _pt = QPointF(_x, 12)
    _js.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, _pt, _pt,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    if not _js.isSliderDown():
        _missed += 1
    _js.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, _pt, _pt,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
assert _missed == 0, ("홈 클릭이 드래그로 안 이어진다", _missed)

# 모든 서브메뉴에 테마가 걸려 있다 (최상위만 걸면 항목 대부분이 옛 모양이다)
_m = ctl.context_menu()
_subs = _m.findChildren(QMenu)
_styled = sum(1 for x in _subs
              if x.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
assert _subs and _styled == len(_subs), (_styled, len(_subs))

# 메뉴의 "설정 닫기"는 실제로 닫아야 한다. 라벨만 바꾸고 동작을 toggle 로
# 두면, 우클릭 시점에 바깥클릭 필터가 이미 닫아 둔 탓에 다시 열린다.
ctl.open_panel()
assert pnl.isVisible()
_far2 = QPointF(9000, 9000)
pnl.eventFilter(ctl.window, QMouseEvent(          # 우클릭이 필터를 먼저 태운다
    QEvent.Type.MouseButtonPress, _far2, _far2, Qt.MouseButton.RightButton,
    Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier))
_menu = ctl.context_menu()
menu.rebuild_into(_menu, ctl)
_first = [a for a in _menu.actions() if a.text()][0]
assert "닫기" in _first.text(), ("방금 닫힌 패널인데 라벨이 열기다", _first.text())
_first.trigger()
assert not pnl.isVisible(), "'설정 닫기'를 눌렀는데 패널이 열렸다"

# 모달이 떠 있으면 바깥클릭으로 안 닫힌다 (색상 대화상자에서 색을 못 고르던 문제)
ctl.open_panel()
_modal = QWidget()
_modal.setWindowModality(Qt.WindowModality.ApplicationModal)
_modal.show()
pnl.eventFilter(_modal, QMouseEvent(
    QEvent.Type.MouseButtonPress, _far2, _far2, Qt.MouseButton.LeftButton,
    Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
assert pnl.isVisible(), "모달 안을 클릭했는데 패널이 닫혔다"
_modal.close()

ctl.close_panel()

import struct
import theme
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QButtonGroup, QLabel, QPushButton, QSlider, QStyle,
)
from PySide6.QtWidgets import QStyleOptionSlider

# 7o) 접근성 회귀 — 승인자가 짚은 항목들
ctl.open_panel()
ctl.settings.track_enabled = True
ctl.settings.bg_mode = "color"
ctl.settings.shape = "rect"
pnl.sync()
pnl.resize(pnl.sizeHint())
pnl.show()
pnl.activateWindow()  # 활성 창이 아니면 app.focusWidget() 이 None 이라 화살표 검사가 헛돈다
app.processEvents()

# 닫기 버튼이 지정한 크기 그대로 그려지는지. QSS 의 min-height 26px 가
# setFixedSize 를 이겨서 24x26 으로 나오던 적이 있다.
# 패널 자신이 Tab 정거장이 되면 표시 없는 포커스 상태가 생긴다.
# findChildren 에는 자기가 안 잡히므로 따로 확인한다.
assert pnl.focusPolicy() == Qt.FocusPolicy.NoFocus, (
    "패널 창 자신이 Tab 정거장이다", pnl.focusPolicy())
assert pnl.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating), (
    "패널이 뜰 때 포커스를 뺏는다 — 방송 중에 쓰는 앱이다")

_close = pnl.findChild(QPushButton, "Close")
assert _close is not None, "닫기 버튼에 objectName 이 없다"
assert (_close.width(), _close.height()) == (theme.CLOSE_BTN, theme.CLOSE_BTN), (
    "닫기 버튼 크기", _close.width(), _close.height())

# Tab 으로 컨트롤에 도달할 수 있는지 (전부 ClickFocus 여서 0개이던 적이 있다)
_w = pnl.nextInFocusChain()
for _ in range(500):
    if (_w.focusPolicy() != Qt.FocusPolicy.NoFocus and _w.isVisible()
            and _w.isEnabled()):
        _w.setFocus()
        break
    _w = _w.nextInFocusChain()
_reach, _seen = [], set()
for _ in range(300):
    _w = pnl.focusWidget()
    if _w is None or id(_w) in _seen:
        break
    _seen.add(id(_w))
    _reach.append(_w)
    if not pnl.focusNextChild():
        break
assert any(isinstance(w, QSlider) for w in _reach), "Tab 이 슬라이더에 못 간다"

# 포커스 가능한 컨트롤은 Tab 이든 화살표든 **전부** 닿아야 한다. 개수 임계값
# (>= 12 같은 것)은 실제 개수보다 낮으면 아무것도 못 잡는다.
_focusable = [w for w in pnl.findChildren(QWidget)
              if w.focusPolicy() != Qt.FocusPolicy.NoFocus
              and w.isVisible() and w.isEnabled()]
# 배타 그룹 소속이라는 사실만으로 통과시키면 안 된다 — 그룹 멤버를
# ClickFocus 로 바꿔도 소속은 그대로라 Tab 도달 0개인 채 통과한다.
# 실제로 화살표를 눌러 포커스가 옮겨가는지 본다.
def _panel_state():
    """조건부 회색 처리를 전부 풀어 둔 상태로 되돌린다."""
    ctl.settings.track_enabled = True
    ctl.settings.bg_mode = "color"
    ctl.settings.shape = "rect"
    pnl.sync()
    app.processEvents()

def _arrow_reach(start):
    """start 에서 화살표로 닿는 위젯 집합.

    화살표는 **값도 바꾼다** — 배타 그룹에서는 그게 표준 동작이다(라디오 버튼과
    같다). 그래서 배경 모드 줄을 훑고 나면 배경이 '끄기'가 되고, 그 순간 배경 색
    견본이 회색 처리돼 다음 차례에 도달 불가로 잡힌다. 검사가 스스로 만든
    상태 변화였다. 매번 상태를 되돌려 놓고 걷는다.
    """
    got = set()
    _panel_state()
    start.setFocus(Qt.FocusReason.TabFocusReason)
    app.processEvents()
    # 네 방향 전부 본다. 앞으로만 걸으면 그룹의 마지막 항목이 Tab 정거장일 때
    # (그 항목이 선택돼 있을 때) 앞쪽 형제들에 영영 못 닿는다.
    for _key in (Qt.Key.Key_Right, Qt.Key.Key_Down,
                 Qt.Key.Key_Left, Qt.Key.Key_Up):
        # app.focusWidget() 이 아니라 pnl.focusWidget() 을 쓴다 — 스모크에는
        # 오버레이·메뉴 같은 다른 창이 있어서 패널이 활성 창이 아닐 수 있고,
        # 그러면 app 쪽은 None 을 돌려줘 화살표 검사가 통째로 헛돈다.
        _cur = pnl.focusWidget()
        for _ in range(20):
            if _cur is None:
                break
            got.add(id(_cur))
            for _typ in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                app.sendEvent(_cur, QKeyEvent(_typ, _key,
                                              Qt.KeyboardModifier.NoModifier))
            app.processEvents()
            _nxt = pnl.focusWidget()
            if _nxt is _cur:
                break
            _cur = _nxt
    return got

# 한 번만 훑으면 안 된다. 이번 패스에서 새로 도달한 위젯에서 다시 뻗어나갈 수
# 있으므로 더 늘어나지 않을 때까지 반복한다(고정점).
_reachable = set(_seen)
for _round in range(6):
    _before = len(_reachable)
    for _w in list(_focusable):
        if id(_w) in _reachable:
            _reachable |= _arrow_reach(_w)
    if len(_reachable) == _before:
        break
_panel_state()  # 화살표 탐색이 바꿔 놓은 값을 원복하고 이어서 검사한다
_unreached = [w for w in _focusable if id(w) not in _reachable]
assert not _unreached, (
    "Tab 으로도 화살표로도 못 가는 컨트롤",
    [(w.__class__.__name__, w.accessibleName()) for w in _unreached])

# 포커스 표시가 실제 픽셀로 보이는지. QSS 에 :focus 규칙이 없어 포커스가
# 있으나 마나이던 적이 있다 (WCAG 2.4.7).
def _pixdiff(a, b):
    n = 0
    for y in range(min(a.height(), b.height())):
        for x in range(min(a.width(), b.width())):
            if a.pixel(x, y) != b.pixel(x, y):
                n += 1
    return n

# 클래스별로 하나씩 고르면(next(... isinstance ...)) 안 된다 — ToggleSwitch 와
# Swatch 는 둘 다 QPushButton 서브클래스였는데 하필 그 둘만 링이 없었고,
# 첫 QPushButton 인 닫기 버튼에서 멈춰 한 번도 검사되지 않았다. 전수로 돈다.
for _t in _focusable:
    if _t.__class__.__name__ == "QScrollArea":
        continue
    _what = "%s[%s]" % (_t.__class__.__name__, _t.accessibleName())
    _t.clearFocus()
    app.processEvents()
    _off = _t.grab().toImage()
    _size_off = _t.size()
    _t.setFocus(Qt.FocusReason.TabFocusReason)
    app.processEvents()
    _on = _t.grab().toImage()
    assert _pixdiff(_off, _on) > 0, "%s 에 포커스 링이 안 보인다" % _what
    # 링이 굵어지며 위젯이 커지면 Tab 마다 레이아웃이 튄다
    assert _t.size() == _size_off, ("%s 가 포커스로 크기가 변한다" % _what,
                                    _size_off, _t.size())

# 슬라이더는 바깥 크기뿐 아니라 groove 기하도 안 움직여야 한다. QSS 테두리를
# 쓰면 내용 사각형이 줄어 채워진 구간이 1px 흔들리고 클릭->값 매핑까지 바뀐다.
def _groove(w):
    _o = QStyleOptionSlider()
    w.initStyleOption(_o)
    return w.style().subControlRect(
        QStyle.ComplexControl.CC_Slider, _o, QStyle.SubControl.SC_SliderGroove, w)

_gs = next(w for w in _focusable if isinstance(w, QSlider))
_gs.clearFocus()
app.processEvents()
_g_off = _groove(_gs)
_gs.setFocus(Qt.FocusReason.TabFocusReason)
app.processEvents()
assert _groove(_gs) == _g_off, ("포커스가 groove 를 민다", _g_off, _groove(_gs))

# 커스텀 위젯이 지정한 크기 그대로 그려지는지. 전역 QPushButton 규칙의
# min-height 가 setFixedSize 를 이겨 아래 4px 가 비고 라벨보다 2px 뜬 적이 있다.
from panel import ToggleSwitch, Swatch
# 기대값을 위젯 자신의 상수에서 가져오면(_tg.W 끼리 비교) 상수가 틀려도
# 항상 통과한다. 숫자를 직접 적는다.
_tg = pnl.findChild(ToggleSwitch)
assert (_tg.TRACK_W, _tg.TRACK_H) == (44, 24), ("스위치 트랙 크기", _tg.TRACK_W, _tg.TRACK_H)
assert (_tg.width(), _tg.height()) == (50, 30), ("ToggleSwitch 크기", _tg.width(), _tg.height())
_sw0 = pnl.findChildren(Swatch)[0]
assert _sw0.SIZE == 24, ("견본 크기", _sw0.SIZE)  # WCAG 2.2 SC 2.5.8
assert (_sw0.width(), _sw0.height()) == (30, 30), ("Swatch 크기", _sw0.width(), _sw0.height())
# 링을 그릴 여백이 없으면 트랙 위에 겹쳐 그려져 ON 상태(ACCENT)에서 1.53:1 이 된다
assert theme.FOCUS_PAD >= theme.FOCUS_RING, ("포커스 링 여백 부족", theme.FOCUS_PAD)
# 같은 줄 라벨과 세로 중심이 맞는지
_lbl = _sw0.parent().findChild(QLabel)
assert abs((_lbl.y() + _lbl.height() // 2) - (_sw0.y() + _sw0.height() // 2)) <= 1, (
    "견본이 라벨과 세로로 안 맞는다",
    _lbl.y() + _lbl.height() // 2, _sw0.y() + _sw0.height() // 2)

# 화살표 키로 값이 바뀌는지
_sp = next(w for w in pnl.findChildren(QSlider) if w.accessibleName() == "속도")
_sp.setFocus(Qt.FocusReason.TabFocusReason)
_before = _sp.value()
for _typ in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
    app.sendEvent(_sp, QKeyEvent(_typ, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier))
app.processEvents()
assert _sp.value() != _before, "화살표 키로 슬라이더 값이 안 바뀐다"

# 스크린리더용 이름 — 그림만 있는 컨트롤은 이름이 없으면 읽을 게 없다
for _w in pnl.findChildren(QWidget):
    if _w.focusPolicy() == Qt.FocusPolicy.NoFocus or not _w.isVisible():
        continue
    if _w.__class__.__name__ == "QWidget":  # 레이아웃 컨테이너
        continue
    assert _w.accessibleName(), ("이름 없는 컨트롤", _w.__class__.__name__)

# 직접 고른 색으로 바꾸면 견본의 선택 링이 남으면 안 된다. 배타 QButtonGroup 이
# "체크된 버튼의 해제"를 무시해서 가짜 선택이 남던 문제.
from panel import Swatch
_row = pnl.bg_color
_row.set_value(_row._swatches[0].color)
assert _row._swatches[0].isChecked()
_row.set_value("#123456")
assert not any(sw.isChecked() for sw in _row._swatches if sw.color is not None), (
    "저장된 색과 다른데 견본에 선택 링이 남았다")

# 어떤 상태에서도 내용이 뷰포트보다 넓어지면 안 된다. 가로 스크롤바를 꺼 둬서
# 넘치면 오른쪽이 잘린 채 복구가 안 된다 — 줄바꿈 안 켠 라벨 하나가 모델 없는
# 설치에서 43개 위젯을 40px 씩 잘라먹은 적이 있다.
_inner = pnl._scroll.widget()
for _missing in (True, False):
    pnl.track_missing.setVisible(_missing)
    app.processEvents()
    _need = _inner.minimumSizeHint().width()
    _have = pnl._scroll.viewport().width()
    assert _need <= _have, (
        "패널 내용이 뷰포트보다 넓다 (오른쪽이 잘린다)",
        "모델없음표시=%s" % _missing, _need, _have)
pnl.sync()

# 오버레이를 클릭했다고 패널이 닫히면 패널을 연 채로 창을 못 옮긴다
ctl.window.setGeometry(100, 100, 320, 180)
_inside = QPointF(160, 150)
pnl.eventFilter(ctl.window, QMouseEvent(
    QEvent.Type.MouseButtonPress, _inside, _inside, Qt.MouseButton.LeftButton,
    Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
assert pnl.isVisible(), "오버레이를 클릭했는데 패널이 닫혔다"
ctl.close_panel()

# 7p) 명도 대비 — 토큰을 손댈 때 눈으로 못 보고 깨뜨리는 걸 막는다
def _lum(h):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

def _contrast(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)

for _fg, _bg, _need, _what in (
    (theme.FG, theme.BG_CARD, 4.5, "본문"),
    (theme.FG_MUTED, theme.BG_CARD, 4.5, "보조 글자"),
    (theme.ACCENT, theme.BG_CARD, 4.5, "값 표시"),
    (theme.ON_ACCENT, theme.MENU_HOVER, 4.5, "메뉴 하이라이트 위 글자"),
    ("#0F1115", theme.ACCENT, 4.5, "선택된 버튼 글자"),
    (theme.MENU_HOVER, theme.MENU_BG, 3.0, "메뉴 하이라이트"),
    (theme.CONTROL_BORDER, theme.BG_CARD, 3.0, "버튼 경계"),
    (theme.FOCUS, theme.BG_CARD, 3.0, "포커스 링(카드 위)"),
    (theme.FOCUS, theme.TRACK, 3.0, "포커스 링(버튼 위)"),
    (theme.KNOB, theme.BG_CARD, 3.0, "슬라이더 핸들"),
    # 상태별 조합도 본다 — 여기가 빠져 있어 hover/pressed 미달을 놓쳤다
    (theme.BG_CARD, theme.DANGER, 4.5, "닫기 hover 글자"),
    ("#0F1115", theme.ACCENT_PRESS, 4.5, "눌린 버튼 글자"),
    (theme.FG, theme.CONTROL_HOVER, 4.5, "hover 버튼 글자"),
):
    _got = _contrast(_fg, _bg)
    assert _got >= _need, ("%s 대비 미달" % _what, round(_got, 2), _need)

# 토큰만 재면 QSS 가 그 토큰을 실제로 쓰는지는 모른다 — `:pressed` 에 글자색을
# 안 줘서 상속되던 3.55:1 을 토큰 검사가 통째로 놓쳤다. QSS 블록을 직접 읽어
# 글자/배경 짝을 만들어 잰다.
def _qss_blocks(qss):
    blocks, i = {}, 0
    while True:
        open_at = qss.find("{", i)
        if open_at < 0:
            break
        close_at = qss.find("}", open_at)
        sel = qss[i:open_at].strip().strip(",").splitlines()[-1].strip()
        decls = {}
        for _d in qss[open_at + 1:close_at].split(";"):
            if ":" in _d:
                _k, _v = _d.split(":", 1)
                decls[_k.strip()] = _v.strip()
        for _one in [x.strip() for x in sel.split(",") if x.strip()]:
            blocks[_one] = decls
        i = close_at + 1
    return blocks

_BLOCKS = _qss_blocks(theme.QSS)

def _resolve(sel, prop):
    """`QPushButton#Close:hover` -> `QPushButton#Close` -> `QPushButton` 순으로 상속."""
    chain = [sel]
    if ":" in sel:
        chain.append(sel.split(":")[0])
    if "#" in chain[-1]:
        chain.append(chain[-1].split("#")[0])
    for _c in chain:
        _v = _BLOCKS.get(_c, {}).get(prop)
        if not _v:
            continue
        # 투명 배경은 뒤에 있는 면이 실제 배경이다 (닫기 버튼 = 카드 위)
        if _v == "transparent":
            return theme.BG_CARD
        if _v.startswith("#"):
            return _v
        return None  # 그라디언트 등 — 자동 판정 대상 아님
    return None

_checked = 0
for _sel, _decls in _BLOCKS.items():
    if not _sel.startswith(("QPushButton", "QMenu::item", "QToolTip")):
        continue
    if ":disabled" in _sel:  # 비활성은 일부러 흐리다
        continue
    _bg, _fg = _resolve(_sel, "background"), _resolve(_sel, "color")
    if not _bg or not _fg:
        continue
    _got = _contrast(_fg, _bg)
    _checked += 1
    assert _got >= 4.5, ("QSS 글자 대비 미달", _sel, _fg, "on", _bg, round(_got, 2))
assert _checked >= 6, ("QSS 대비 검사가 블록을 못 찾았다", _checked)

# 미선택 버튼의 윤곽은 hover 에서도 3:1 이어야 한다. hover 배경색으로
# border-color 까지 덮으면 1.98:1 로 떨어져 `border: none` 이던 시절로 돌아간다.
def _border_color(sel):
    _v = _BLOCKS.get(sel, {}).get("border-color") or _BLOCKS.get(sel, {}).get("border")
    if _v is None and ":" in sel:  # 상태 블록이 안 건드리면 기본 블록을 물려받는다
        return _border_color(sel.split(":")[0])
    if _v is None:
        return None
    _tok = _v.split()[-1]
    return _tok if _tok.startswith("#") else None

for _sel in ("QPushButton", "QPushButton:hover"):
    _bd = _border_color(_sel)
    # None 이면 `border: none` 이거나 아예 선언이 없다는 뜻이다. 배경만으로는
    # 1.52:1 이라 윤곽이 안 보인다 — 반려 사유였다. 통과시키면 안 된다.
    assert _bd is not None, ("버튼 경계가 없다 (배경만으로는 1.52:1)", _sel)
    assert _contrast(_bd, theme.BG_CARD) >= 3.0, (
        "버튼 경계 대비 미달", _sel, _bd, round(_contrast(_bd, theme.BG_CARD), 2))

# 링이 그려지기만 하면 되는 게 아니라 **보여야** 한다. 배타 그룹의 Tab
# 정거장은 항상 체크된 버튼이라, 강조색 위에서 안 보이면 키보드 사용자는
# 100% 그 경우를 만난다. FOCUS 는 ACCENT 와 1.53:1 이다.
assert _contrast(theme.FOCUS_ON_ACCENT, theme.ACCENT) >= 3.0, (
    "선택된 버튼 위 포커스 링이 안 보인다",
    round(_contrast(theme.FOCUS_ON_ACCENT, theme.ACCENT), 2))
assert _contrast(theme.FOCUS_ON_ACCENT, theme.ACCENT_PRESS) >= 3.0, (
    "눌린 버튼 위 포커스 링이 안 보인다",
    round(_contrast(theme.FOCUS_ON_ACCENT, theme.ACCENT_PRESS), 2))
assert _contrast(theme.FOCUS, theme.BG_CARD) >= 3.0
_fring = _BLOCKS.get("QPushButton:checked:focus", {}).get("border-color")
assert _fring == theme.FOCUS_ON_ACCENT, ("체크 상태 포커스 링 규칙이 없다", _fring)

# hover 를 selected 와 같은 계열로 두면 마우스만 얹어도 골라진 것처럼 보인다.
# MENU_HOVER(파랑)를 버튼에 그대로 쓰다가 ACCENT 와 1.48:1 이 된 적이 있다.
assert theme.CONTROL_HOVER != theme.MENU_HOVER, "버튼 hover 가 메뉴 하이라이트와 같다"
assert _contrast(theme.CONTROL_HOVER, theme.ACCENT) >= 2.0, (
    "hover 배경이 선택 배경과 구분되지 않는다",
    round(_contrast(theme.CONTROL_HOVER, theme.ACCENT), 2))

# 7q) 아이콘 — 16px 에서 사람으로 읽히는지가 유일한 기준이다
import tray
for _s in (16, 20, 24):
    _img = tray.draw_icon_pixmap(_s).toImage()
    _col, _runs, _prev = _s // 2, [], None
    for _y in range(_s):
        _c = _img.pixelColor(_col, _y)
        _k = ("흰" if (_c.alpha() > 128 and _c.red() > 200 and _c.green() > 200)
              else ("파" if _c.alpha() > 128 else "빈"))
        if _k != _prev:
            _runs.append([_k, 0])
            _prev = _k
        _runs[-1][1] += 1
    # 파(바탕) 흰(머리) 파(틈) 흰(어깨) — 틈이 사라지면 눈사람이 얼룩이 된다
    _kinds = [k for k, _ in _runs]
    assert _kinds == ["파", "흰", "파", "흰"], ("%dpx 실루엣이 뭉갰다" % _s, _runs)
    assert _runs[2][1] >= 1, ("%dpx 머리-어깨 틈이 없다" % _s)
    assert _runs[1][1] >= 3, ("%dpx 머리가 너무 작다" % _s, _runs[1][1])
    # 가로로도 확인한다 — 세로 1열만 보면 좌우로 번진 건 못 잡는다.
    _hrow = _runs[1]
    _head_y = sum(n for _, n in _runs[:1])
    _wide = sum(1 for _x in range(_s)
                if _img.pixelColor(_x, _head_y).red() > 200
                and _img.pixelColor(_x, _head_y).alpha() > 128)
    assert 2 <= _wide <= _s * 0.60, ("%dpx 머리 폭이 이상하다" % _s, _wide)

# 큰 사이즈도 틈이 살아 있어야 한다. 작은 쪽은 1픽셀로 강제되므로 GAP 상수가
# 0 이 돼도 안 드러난다 — 32/48 에서만 잡힌다.
for _s in (32, 48):
    _img = tray.draw_icon_pixmap(_s).toImage()
    _col, _runs, _prev = _s // 2, [], None
    for _y in range(_s):
        _c = _img.pixelColor(_col, _y)
        _k = ("흰" if (_c.alpha() > 128 and _c.red() > 200 and _c.green() > 200)
              else ("파" if _c.alpha() > 128 else "빈"))
        if _k != _prev:
            _runs.append([_k, 0])
            _prev = _k
        _runs[-1][1] += 1
    assert [k for k, _ in _runs] == ["파", "흰", "파", "흰"], (
        "%dpx 머리-어깨가 붙었다" % _s, _runs)

# 바탕이 투명 배경 위로 안 새는지 (어깨 타원을 안 자르면 모서리에 흰 조각이 남는다)
_img = tray.draw_icon_pixmap(64).toImage()
for _x, _y in ((0, 0), (63, 0), (0, 63), (63, 63)):
    assert _img.pixelColor(_x, _y).alpha() < 40, ("모서리가 안 둥글다", _x, _y)

_ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.ico")
if os.path.exists(_ico):
    with open(_ico, "rb") as _f:
        _frames = struct.unpack("<H", _f.read(6)[4:6])[0]
    assert _frames == len(tray.ICON_SIZES), ("ico 프레임 수", _frames)

# 8) 단축키 등록/해제가 예외를 내지 않는지
assert isinstance(ctl.hotkeys.failed, list)
ctl.hotkeys.unregister_all()

# 9) 중복 실행 차단 — 같은 이름으로 두 번 잡으면 두 번째는 실패해야 한다.
# 앱이 실제로 쓰는 이름(SINGLE_INSTANCE_KEY)을 쓰면 안 된다. 지금 돌고 있는
# 진짜 앱을 이 테스트가 "두 번째 인스턴스"로 만들어 깨워 버린다.
_key = "WebcamMirror-SmokeTest-%d" % os.getpid()
_first = winapi.SingleInstance(_key)
assert _first.acquired, "첫 인스턴스는 잡혀야 한다"
_second = winapi.SingleInstance(_key)
assert not _second.acquired, "두 번째 인스턴스는 막혀야 한다"

# 두 번째가 보낸 신호가 첫 번째에 도착하는지 (창을 띄우는 그 경로다)
_woken = []
_first.activated.connect(lambda: _woken.append(1))
assert _second.notify_existing(), "첫 인스턴스를 깨우지 못했다"
QTimer.singleShot(120, app.quit)
app.exec()
assert _woken, "activated 신호가 오지 않았다"

# 풀고 나면 다시 잡을 수 있어야 한다 — 안 그러면 앱이 재실행 불가가 된다
_first.release()
_third = winapi.SingleInstance(_key)
assert _third.acquired, "해제 뒤에는 다시 잡혀야 한다"
_third.release()

# 실제 설정 파일을 건드리지 않고 끝낸다
ctl._save_timer.stop()
try:
    app.aboutToQuit.disconnect(ctl._on_about_to_quit)
except (RuntimeError, TypeError):
    pass
if os.path.exists(settings.SETTINGS_PATH):
    os.remove(settings.SETTINGS_PATH)
settings.SETTINGS_PATH = _real_settings_path

print("SMOKE OK")
