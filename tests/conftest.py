import sys
from pathlib import Path

# Ensure the `src` folder is on sys.path when running pytest so imports like
# `from brokers import ...` or `from core import ...` work without needing to
# install the package. This keeps tests consistent with running tools using
# PYTHONPATH=$(pwd)/src.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
