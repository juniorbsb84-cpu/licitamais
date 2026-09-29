import pathlib
import sys

ROOT = pathlib.Path(__file__).parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
