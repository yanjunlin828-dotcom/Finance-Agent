"""W0 tests load presentation fixtures only; no Agent or provider invocation."""
from pathlib import Path
import json
import sys

import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP_ROOT / "src"))


@pytest.fixture
def fixture_data():
    def load(name: str):
        return json.loads((APP_ROOT / "web/fixtures" / (name + ".json")).read_text(encoding="utf-8"))
    return load
