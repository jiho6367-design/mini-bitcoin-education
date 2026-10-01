"""화면 단위 테스트의 예시 데이터가 현재 백엔드의 응답 형태와 같은지 확인한다."""

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "export_frontend_fixtures.py"


def load_exporter():
    spec = importlib.util.spec_from_file_location("export_frontend_fixtures", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_fixtures_match_the_backend():
    exporter = load_exporter()
    committed = json.loads(exporter.TARGET.read_text(encoding="utf-8"))
    # JSON으로 한 번 바꿔 튜플·정수 키 같은 Python 고유 형태의 차이를 없앤다.
    assert json.loads(json.dumps(exporter.build())) == committed


def test_fixtures_never_contain_private_keys():
    exporter = load_exporter()
    text = exporter.TARGET.read_text(encoding="utf-8")
    assert exporter.ALICE.private_key not in text
    assert exporter.BOB.private_key not in text
    assert "privateKey" not in text
