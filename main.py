"""Entry point for the iRacing Coach overlay trainer.

Usage::

    python main.py            # attach to a running iRacing session
    python main.py --demo     # run with synthetic telemetry (no game needed)

Press **F8** to toggle between edit mode (draggable) and locked mode (clicks
pass through to the game).  **Esc** closes the overlay.
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
    references.load_default()

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