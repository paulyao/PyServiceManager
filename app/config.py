"""Platform configuration."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SERVICES_DIR = DATA_DIR / "services"
MODULES_DIR = DATA_DIR / "modules"
DB_PATH = DATA_DIR / "platform.db"
TEMPLATES_DIR = BASE_DIR / "templates"

HOST = os.getenv("PYSERVICE_HOST", "0.0.0.0")
PORT = int(os.getenv("PYSERVICE_PORT", "8900"))

API_PREFIX = "/api/v1"

# Auto-detect Python interpreter path (avoid broken pyenv shims)
import sys
DEFAULT_PYTHON_PATH = os.getenv("PYSERVICE_PYTHON", sys.executable)
