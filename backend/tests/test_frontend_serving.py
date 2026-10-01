"""백엔드 한 서버가 빌드된 화면과 API를 함께 제공하는지 확인한다."""

import pytest
from fastapi.testclient import TestClient

from mini_bitcoin_backend.api import app as app_module
from mini_bitcoin_backend.api.app import FRONTEND_DIR_ENV, create_app, default_frontend_dir

PAGE = "<!doctype html><title>Mini Bitcoin</title><div id=root></div>"


@pytest.fixture
def dist(tmp_path):
    """빌드 결과와 같은 구조의 임시 폴더."""
    folder = tmp_path / "dist"
    (folder / "assets").mkdir(parents=True)
    (folder / "index.html").write_text(PAGE, encoding="utf-8")
    (folder / "assets" / "index-abc123.js").write_text("console.log('app')", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("화면 폴더 밖의 파일", encoding="utf-8")
    return folder


@pytest.fixture
def client(dist):
    return TestClient(create_app(frontend_dir=dist))


def test_serves_the_page_and_its_assets(client):
    page = client.get("/")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert page.text == PAGE
    # 이름에 해시가 없는 HTML은 매번 확인해 다시 빌드한 화면이 바로 보이게 한다.
    assert page.headers["cache-control"] == "no-cache"
    assert client.get("/index.html").text == PAGE

    script = client.get("/assets/index-abc123.js")
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert script.text == "console.log('app')"
    assert "cache-control" not in script.headers


def test_api_keeps_working_and_takes_priority(client, dist):
    assert client.get("/api/health").json()["status"] == "ok"
    # 화면 폴더에 api라는 이름의 파일이 있어도 API 경로가 우선한다.
    (dist / "api").mkdir()
    (dist / "api" / "health").write_text("가짜", encoding="utf-8")
    assert client.get("/api/health").json()["status"] == "ok"

    lab = client.post("/api/labs", json={"mode": "example", "difficulty": 1})
    assert lab.status_code == 201
    lab_id = lab.json()["lab"]["labId"]
    assert client.get(f"/api/labs/{lab_id}").json()["lab"]["phase"] == "running"
    missing = client.get("/api/labs/unknown")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "LAB_NOT_FOUND"


def test_unknown_paths_and_traversal_get_the_error_envelope(client):
    for path in ("/missing.js", "/assets/none.css", "/api/nowhere"):
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.json()["error"]["code"] == "HTTP_ERROR", path
    for path in ("/..%2Fsecret.txt", "/assets/..%2F..%2Fsecret.txt", "/%2e%2e/secret.txt"):
        response = client.get(path)
        assert response.status_code == 404, path
        assert "화면 폴더 밖의 파일" not in response.text


def test_without_a_build_only_the_api_is_served():
    client = TestClient(create_app())
    root = client.get("/")
    assert root.status_code == 404
    assert root.json()["error"]["code"] == "HTTP_ERROR"
    assert client.get("/api/health").status_code == 200


def test_default_folder_needs_an_index_page(dist, tmp_path, monkeypatch):
    monkeypatch.setenv(FRONTEND_DIR_ENV, str(dist))
    assert default_frontend_dir() == dist
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv(FRONTEND_DIR_ENV, str(empty))
    assert default_frontend_dir() is None
    monkeypatch.setenv(FRONTEND_DIR_ENV, str(tmp_path / "does-not-exist"))
    assert default_frontend_dir() is None


def test_default_folder_is_the_repository_build(monkeypatch):
    monkeypatch.delenv(FRONTEND_DIR_ENV, raising=False)
    expected = app_module.Path(app_module.__file__).resolve().parents[4] / "frontend" / "dist"
    assert expected.parent.name == "frontend"
    assert (expected.parent / "package.json").is_file()
    # 빌드 여부는 PC마다 다르다. 빌드가 있으면 그 폴더를, 없으면 None을 돌려준다.
    assert default_frontend_dir() == (expected if (expected / "index.html").is_file() else None)


def test_command_line_options(dist, monkeypatch, capsys):
    served = {}

    def fake_run(application, host, port):
        served.update(app=application, host=host, port=port)

    monkeypatch.setattr(app_module.uvicorn, "run", fake_run)
    monkeypatch.setenv(FRONTEND_DIR_ENV, str(dist))

    monkeypatch.setattr("sys.argv", ["mini-bitcoin-backend", "--port", "8123"])
    app_module.main()
    assert (served["host"], served["port"]) == ("127.0.0.1", 8123)
    assert TestClient(served["app"]).get("/").text == PAGE
    assert "화면과 API를 함께 제공합니다: http://127.0.0.1:8123" in capsys.readouterr().out

    monkeypatch.setattr("sys.argv", ["mini-bitcoin-backend", "--api-only"])
    app_module.main()
    assert TestClient(served["app"]).get("/").status_code == 404
    assert "API만 제공합니다(--api-only)" in capsys.readouterr().out

    monkeypatch.setenv(FRONTEND_DIR_ENV, str(dist / "assets"))
    monkeypatch.setattr("sys.argv", ["mini-bitcoin-backend"])
    app_module.main()
    assert TestClient(served["app"]).get("/").status_code == 404
    assert "npm.cmd run build" in capsys.readouterr().out

    monkeypatch.setattr("sys.argv", ["mini-bitcoin-backend", "--frontend", str(dist)])
    app_module.main()
    assert TestClient(served["app"]).get("/").text == PAGE

    monkeypatch.setattr("sys.argv", ["mini-bitcoin-backend", "--frontend", str(dist / "assets")])
    with pytest.raises(SystemExit):
        app_module.main()
    assert "index.html이 없습니다" in capsys.readouterr().err
