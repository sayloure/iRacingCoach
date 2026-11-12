"""Entry point for the iRacing Coach overlay trainer.

Usage::

    python main.py            # attach to a running iRacing session
    python main.py --demo     # run with synthetic telemetry (no game needed)

The overlay starts unlocked (draggable, buttons clickable).  Press **F8** to
toggle click-through mode and **Ctrl+F12** to quit; both work globally even
when iRacing has the focus.
"""

from __future__ import annotations

import argparse
import signal
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from overlay_window import OverlayWindow
from reference_manager import ReferenceManager
from telemetry_reader import TelemetryReader


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="iRacing Coach overlay trainer")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="generate synthetic telemetry instead of reading iRacing",
    )
    parser.add_argument(
        "--exit-after",
        type=float,
        default=0.0,
        help="auto-close after N seconds (used for smoke testing)",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    app = QApplication(sys.argv[:1])
    # Allow Ctrl+C to terminate the Qt loop cleanly.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    reader = TelemetryReader(demo=args.demo)
    reader.connect()

    references = ReferenceManager()
    if not references.load_default() and args.demo:
        # No saved lap on disk: install a synthetic reference so the demo shows
        # overlaid traces, a live time delta and a brake-zone cue right away.
        references.load_synthetic(lap_length=reader._demo_lap_length)

    window = OverlayWindow(reader, references)
    window.show()

    if args.exit_after > 0:
        QTimer.singleShot(int(args.exit_after * 1000), app.quit)

    try:
        code = app.exec()
    finally:
        reader.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())