"""Make the shopkit under evaluation importable: the agent's workspace."""
import os
import sys
from pathlib import Path

WORKSPACE = Path(os.environ.get("RIMURU_WORKSPACE", "data/practice")).resolve()
sys.path.insert(0, str(WORKSPACE))
