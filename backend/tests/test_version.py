"""화면과 백엔드는 같은 버전으로 함께 배포한다."""

import json
import tomllib
from pathlib import Path

from mini_bitcoin_backend import __version__

ROOT = Path(__file__).resolve().parents[2]


def test_backend_and_frontend_versions_match():
    pyproject = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
    package = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    lock = tomllib.loads((ROOT / "backend" / "uv.lock").read_text(encoding="utf-8"))
    locked = next(p for p in lock["package"] if p["name"] == "mini-bitcoin-backend")
    assert pyproject["project"]["version"] == package["version"]
    assert locked["version"] == package["version"]
    assert __version__ == package["version"]
