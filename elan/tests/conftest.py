import os
import sys
from pathlib import Path

# Tests never need a visible window; CI runners on Linux have no display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# tests/data/generate.py builds generated test files; it is not inside a package, so make it
# importable the way tests/support.py already is (tests/ itself is on sys.path via pytest).
sys.path.insert(0, str(Path(__file__).resolve().parent / "data"))
