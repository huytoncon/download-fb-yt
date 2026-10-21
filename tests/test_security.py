import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import DownloadService, MergeService

PAGES_ORIGIN = "https://user.github.io"


def make_client(tmp_path, **kwargs):
    downloads = DownloadService(tmp_path / "dl", max_workers=1, downloader=lambda *_: None)
    merges = MergeService(downloads, tmp_path / "merged")
    return TestClient(create_app(downloads, merges, **kwargs))


@pytest.fixture
def secured(tmp_path):
    with make_client(tmp_path, cors_origins=[PAGES_ORIGIN], api_key="s3cret") as client:
        yield client


def test_api_requires_key_when_configured(secured):
    res = secured.get("/api/jobs")
    assert res.status_code == 401
    assert res.json()["error"] == "Sai hoặc thiếu API key"
    assert secured.get("/api/jobs", headers={"X-API-Key": "wrong"}).status_code == 401


def test_api_accepts_key_in_header(secured):
    assert secured.get("/api/health", headers={"X-API-Key": "s3cret"}).json()["data"]["auth"] is True
    assert secured.get("/api/jobs", headers={"X-API-Key": "s3cret"}).status_code == 200


def test_query_key_only_accepted_on_file_routes(secured):
    assert secured.get("/api/jobs?key=s3cret").status_code == 401
    assert secured.delete("/api/jobs/abc/file?key=s3cret").status_code == 401
    # key đúng qua query trên route file -> qua được bước xác thực, 404 vì job không tồn tại
    assert secured.get("/api/jobs/abc/file?key=s3cret").status_code == 404
    assert secured.get("/api/merges/abc/file?key=wrong").status_code == 401


def test_cors_without_api_key_refuses_to_start(tmp_path):
    with pytest.raises(RuntimeError, match="L2A_API_KEY"):
        make_client(tmp_path, cors_origins=[PAGES_ORIGIN], api_key="")


def test_api_docs_are_disabled(secured):
    assert secured.get("/docs").status_code == 404
    assert secured.get("/openapi.json").status_code == 404


def test_frontend_is_public_even_with_key(secured):
    assert secured.get("/").status_code == 200


def test_cors_preflight_allows_configured_origin(secured):
    res = secured.options(
        "/api/jobs",
        headers={
            "Origin": PAGES_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-api-key",
        },
    )
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == PAGES_ORIGIN


def test_cors_headers_present_on_unauthorized_response(secured):
    res = secured.get("/api/jobs", headers={"Origin": PAGES_ORIGIN})
    assert res.status_code == 401
    assert res.headers["access-control-allow-origin"] == PAGES_ORIGIN


def test_cors_rejects_unknown_origin(secured):
    res = secured.get("/api/jobs", headers={"Origin": "https://evil.example", "X-API-Key": "s3cret"})
    assert "access-control-allow-origin" not in res.headers


def test_no_cors_and_no_key_by_default(tmp_path):
    with make_client(tmp_path, cors_origins=[], api_key="") as client:
        res = client.get("/api/jobs", headers={"Origin": PAGES_ORIGIN})
        assert res.status_code == 200
        assert "access-control-allow-origin" not in res.headers
