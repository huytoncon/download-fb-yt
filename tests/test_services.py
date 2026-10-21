import threading
import time

import pytest

from app.downloader import DownloadResult
from app.services import DownloadService, MergeService, QueueFullError, purge_generated_files
from app.store import Status

LINK = ("https://youtu.be/x", "youtube")


def wait_until(predicate, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("Hết thời gian chờ")


def test_rejects_new_jobs_when_queue_is_full(tmp_path):
    release = threading.Event()

    def blocking(request, on_progress):
        release.wait(5)
        raise RuntimeError("stop")

    service = DownloadService(tmp_path, max_workers=1, downloader=blocking, max_pending=2)
    service.submit([LINK, LINK], "mp3", 128)
    with pytest.raises(QueueFullError):
        service.submit([LINK], "mp3", 128)
    release.set()
    service.shutdown()


def test_retry_runs_only_once_for_concurrent_requests(tmp_path):
    calls = []

    def failing(request, on_progress):
        calls.append(request.job_id)
        raise RuntimeError("boom")

    service = DownloadService(tmp_path, max_workers=2, downloader=failing)
    job = service.submit([LINK], "mp3", 128)[0]
    wait_until(lambda: service.store.get(job.id).status is Status.ERROR)

    results = [service.retry(job.id), service.retry(job.id)]
    assert sum(r is not None for r in results) == 1
    wait_until(lambda: len(calls) == 2 and service.store.get(job.id).status is Status.ERROR)
    service.shutdown()


def test_failed_download_cleans_partial_files(tmp_path):
    def partial(request, on_progress):
        (request.out_dir / f"{request.job_id}.webm.part").write_text("x")
        raise RuntimeError("network")

    service = DownloadService(tmp_path, max_workers=1, downloader=partial)
    job = service.submit([LINK], "mp3", 128)[0]
    wait_until(lambda: service.store.get(job.id).status is Status.ERROR)
    assert list(tmp_path.iterdir()) == []
    service.shutdown()


def test_unexpected_merge_error_hides_details(tmp_path, tone):
    def ok(request, on_progress):
        path = tone(request.out_dir / f"{request.job_id}.mp3", 0.3)
        return DownloadResult(path, "t", 0.3)

    def broken_merger(*args):
        raise OSError(r"C:\secret\path is broken")

    downloads = DownloadService(tmp_path / "dl", max_workers=2, downloader=ok)
    jobs = downloads.submit([LINK, LINK], "mp3", 128)
    wait_until(lambda: all(downloads.store.get(j.id).status is Status.DONE for j in jobs))

    merges = MergeService(downloads, tmp_path / "merged", merger=broken_merger)
    merge = merges.submit([j.id for j in jobs], "x", "mp3", 128, 0)
    wait_until(lambda: merges.store.get(merge.id).status is Status.ERROR)
    assert merges.store.get(merge.id).error == "Gộp thất bại"
    downloads.shutdown()
    merges.shutdown()


def test_purge_only_removes_generated_files(tmp_path):
    (tmp_path / "0123456789ab.mp3").write_text("x")
    (tmp_path / "0123456789ab.webm.part").write_text("x")
    (tmp_path / "my-song.mp3").write_text("x")
    (tmp_path / "sub").mkdir()
    assert purge_generated_files(tmp_path) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ["my-song.mp3", "sub"]
    assert purge_generated_files(tmp_path / "missing") == 0
