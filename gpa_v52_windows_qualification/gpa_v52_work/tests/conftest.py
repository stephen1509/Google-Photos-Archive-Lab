from pathlib import Path
import sys

# Checkpoints are deliberately runnable without installing the package or
# downloading a build backend.  Final application packaging is a separate lane.
SRC=Path(__file__).resolve().parents[1]/'src'
if str(SRC) not in sys.path:sys.path.insert(0,str(SRC))
