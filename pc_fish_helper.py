"""Launch the current PCfishMate controller from a source checkout."""
import runpy
import sys
from pathlib import Path

internal = Path(__file__).resolve().parent / "internal"
sys.path.insert(0, str(internal))
runpy.run_path(str(internal / "controller.py"), run_name="__main__")
