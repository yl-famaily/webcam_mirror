"""Measure live camera timings without recording images or changing saved settings.
Usage: python camera_diagnostics.py --seconds 20 [--saved-effects]
Delivery timings measure GUI handoff, not physical screen presentation.
"""
import argparse
import json
from PySide6.QtCore import QCoreApplication, QTimer
from camera import CameraThread
from effects import FrameProcessor
from settings import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=20)
    parser.add_argument("--saved-effects", action="store_true")
    parser.add_argument("--camera-index", type=int)
    parser.add_argument("--quality", choices=("480p", "720p", "1080p", "2160p"))
    parser.add_argument("--fps", type=int, choices=(15, 24, 30, 60))
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--fourcc")
    args = parser.parse_args()
    app = QCoreApplication([])
    settings = Settings.load()
    camera_index = settings.camera_index if args.camera_index is None else args.camera_index
    width = args.width or settings.camera_width
    height = args.height or settings.camera_height
    fps = args.fps or settings.camera_fps
    fourcc = args.fourcc or settings.camera_fourcc
    worker = CameraThread(camera_index,
                          FrameProcessor(settings) if args.saved_effects else None,
                          quality=args.quality or settings.camera_quality,
                          fps=fps, width=width, height=height, fourcc=fourcc)
    errors = []
    worker.error.connect(errors.append)
    worker.start()
    QTimer.singleShot(max(1, args.seconds)*1000, app.quit)
    try:
        app.exec()
    finally:
        worker.stop()
    print(json.dumps({"timings": worker.diagnostics(), "errors": errors},
                     ensure_ascii=True, indent=2))
    return 1 if errors else 0

if __name__ == "__main__": raise SystemExit(main())
