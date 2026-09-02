# A path-depth assumption baked in at IMPORT time: this only works if the
# checkout happens to be at least 3 directories deep. A clone into a shallow
# path (e.g. /r, with this file at /r/conftest.py) raises IndexError before
# pytest can even collect a single test.
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
