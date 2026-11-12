"""Temporary smoke test: run the overlay in demo mode and report status."""

from __future__ import annotations

import sys
import traceback

sys.argv = ["main.py", "--demo", "--exit-after", "3"]

try:
    import main

    code = main.main()
    with open("_smoke_result.txt", "w", encoding="utf-8") as fh:
        fh.write(f"OK exit={code}\n")
except Exception:
    with open("_smoke_result.txt", "w", encoding="utf-8") as fh:
        fh.write("ERROR\n")
        traceback.print_exc(file=fh)