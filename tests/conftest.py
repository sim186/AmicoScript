from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

# Keep repository root first so imports like `backend.settings` resolve.
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Also expose backend modules for runtime-style imports (`import state`, `from core...`).
if str(BACKEND_DIR) not in sys.path:
    sys.path.append(str(BACKEND_DIR))
