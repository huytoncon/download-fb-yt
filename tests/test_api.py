import time

import pytest
from fastapi.testclient import TestClient

from app.downloader import DownloadResult
from app.main import create_app
from app.services import DownloadService, MergeService

TIMEOUT_SECONDS = 15


def wait_for(client, path, predicate):
    deadline = time.time() + TIMEOUT_SECONDS
    while time.time() < deadline:
        data = client.get(path).json()["data"]
        if predicate(data):
            return data
        time.sleep(0.1)
    raise AssertionError(f"Hết thời gian chờ {path}: {data}")


@pytest.fixture
def client(tmp_path, tone):
    def fake_downloader(request, on_progress):
        if "fail" in request.url:
            raise RuntimeError("ERROR: Private video. Sign in to confirm")
        on_progress(50.0, "downloading")
        on_progress(99.0, "converting")
        path = tone(request.out_dir / f"{request.job_id}.{request.audio_format}", 1.0)
        return DownloadResult(file_path=path, title=f"Bài {request.url[-1]}", duration=1.0)

    downloads = DownloadService(tmp_path / "dl", max_workers=2, downloader=fake_downloader)
    merges = MergeService(downloads, tmp_path / "merged")
    with TestClient(create_app(downloads, merges)) as test_client:
        yield test_client


def all_finished(jobs):
    return jobs and all(j["status"] in ("done", "error") for j in jobs)


def test_full_flow_multiple_links_then_merge_and_download(client, duration_of, tmp_path):
    res = client.post("/api/jobs", json={"links": "https://youtu.be/1\nhttps://fb.watch/2 https://evil.com/3"})
    body = res.json()
    assert res.status_code == 200 and body["success"]
    assert len(body["data"]["jobs"]) == 2
    assert body["data"]["invalid"] == ["https://evil.com/3"]

    jobs = wait_for(client, "/api/jobs", all_finished)
    assert all(j["status"] == "done" and j["has_file"] for j in jobs)
    assert "file_path" not in jobs[0]

    file_res = client.get(f"/api/jobs/{jobs[0]['id']}/file")
    assert file_res.status_code == 200 and len(file_res.content) > 0
    assert "attachment" in file_res.headers["content-disposition"]

    merge_res = client.post(
        "/api/merges",
        json={"job_ids": [j["id"] for j in jobs], "name": "tổng hợp/../x", "gap_seconds": 1},
    )
    merge = merge_res.json()["data"]
    assert merge["name"] == "tổng hợp_.._x"

    merged = wait_for(client, "/api/merges", lambda ms: ms and ms[0]["status"] in ("done", "error"))[0]
    assert merged["status"] == "done", merged
    out = client.get(f"/api/merges/{merged['id']}/file")
    assert out.status_code == 200
    saved = tmp_path / "check.mp3"
    saved.write_bytes(out.content)
    assert duration_of(saved) == pytest.approx(3.0, abs=0.15)


def test_failed_download_shows_friendly_error_and_can_retry(client):
    client.post("/api/jobs", json={"links": "https://youtu.be/fail"})
    job = wait_for(client, "/api/jobs", all_finished)[0]
    assert job["status"] == "error"
    assert "cần đăng nhập" in job["error"]
    assert client.post(f"/api/jobs/{job['id']}/retry").json()["success"]


def test_rejects_request_without_valid_links(client):
    res = client.post("/api/jobs", json={"links": "hello https://google.com"})
    assert res.status_code == 400
    assert res.json() == {"success": False, "data": None, "error": "Không có link YouTube/Facebook hợp lệ nào"}


def test_rejects_too_many_links(client):
    links = "\n".join(f"https://youtu.be/{i}" for i in range(51))
    res = client.post("/api/jobs", json={"links": links})
    assert res.status_code == 400 and "Tối đa" in res.json()["error"]


def test_rejects_invalid_format(client):
    res = client.post("/api/jobs", json={"links": "https://youtu.be/1", "audio_format": "exe"})
    assert res.status_code == 422 and not res.json()["success"]


def test_merge_requires_finished_jobs(client):
    res = client.post("/api/merges", json={"job_ids": ["nope1", "nope2"]})
    assert res.status_code == 400 and "Không tìm thấy" in res.json()["error"]


def test_delete_job_removes_file(client):
    client.post("/api/jobs", json={"links": "https://youtu.be/1"})
    job = wait_for(client, "/api/jobs", all_finished)[0]
    assert client.delete(f"/api/jobs/{job['id']}").json()["success"]
    assert client.get(f"/api/jobs/{job['id']}/file").status_code == 404
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 404


def test_unknown_file_ids_return_404(client):
    assert client.get("/api/jobs/..%2F..%2Fapp/file").status_code == 404
    assert client.get("/api/merges/xyz/file").status_code == 404


def test_serves_web_ui(client):
    res = client.get("/")
    assert res.status_code == 200 and "Link2Audio" in res.text
