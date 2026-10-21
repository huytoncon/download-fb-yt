"""Hàng đợi xử lý: tải nhiều link song song và gộp audio chạy nền."""

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from app.config import MAX_PENDING_JOBS
from app.downloader import (
    DownloadRequest,
    DownloadResult,
    ProgressCallback,
    download_audio,
    friendly_error,
    remove_partial_files,
)
from app.merger import MergeFailed, merge_audio
from app.store import Job, Merge, Status, Store, new_id

logger = logging.getLogger(__name__)

Downloader = Callable[[DownloadRequest, ProgressCallback], DownloadResult]
Merger = Callable[[list[Path], Path, str, int, float], Path]

_UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_MAX_FILENAME = 120
# Chỉ file do hệ thống tạo (id 12 ký tự hex) mới bị dọn — tránh xoá nhầm nếu cấu hình sai thư mục.
_GENERATED_FILE = re.compile(r"^[0-9a-f]{12}\.")


class MergeValidationError(ValueError):
    pass


class QueueFullError(RuntimeError):
    pass


def safe_filename(name: str, fallback: str) -> str:
    cleaned = _UNSAFE_FILENAME.sub("_", name or "").strip(" ._")
    return cleaned[:_MAX_FILENAME] or fallback


def _remove_file(path: str | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.warning("Không xoá được file %s", path, exc_info=True)


def purge_generated_files(directory: Path) -> int:
    """Xoá file mồ côi do lần chạy trước để lại (trạng thái job chỉ nằm trong bộ nhớ)."""
    if not directory.is_dir():
        return 0
    removed = 0
    for path in directory.iterdir():
        if path.is_file() and _GENERATED_FILE.match(path.name):
            _remove_file(str(path))
            removed += 1
    return removed


def _is_pending(job: Job) -> bool:
    return not job.status.is_finished


class DownloadService:
    def __init__(
        self,
        out_dir: Path,
        max_workers: int,
        downloader: Downloader = download_audio,
        max_pending: int = MAX_PENDING_JOBS,
    ) -> None:
        self.store: Store[Job] = Store()
        self._out_dir = out_dir
        self._downloader = downloader
        self._max_pending = max_pending
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="download")

    def submit(self, links: list[tuple[str, str]], audio_format: str, bitrate: int) -> list[Job]:
        if self.store.count(_is_pending) + len(links) > self._max_pending:
            raise QueueFullError(f"Hàng đợi đầy (tối đa {self._max_pending} link chưa xử lý xong), thử lại sau")
        jobs = [
            self.store.add(Job(id=new_id(), url=url, source=source, audio_format=audio_format, bitrate=bitrate))
            for url, source in links
        ]
        for job in jobs:
            self._executor.submit(self._run, job.id)
        return jobs

    def _run(self, job_id: str) -> None:
        job = self.store.update(job_id, status=Status.DOWNLOADING)
        if job is None:  # đã bị xoá trước khi chạy
            return

        def on_progress(percent: float, stage: str) -> None:
            status = Status.CONVERTING if stage == "converting" else Status.DOWNLOADING
            self.store.update(job_id, progress=round(percent, 1), status=status)

        request = DownloadRequest(job.id, job.url, job.audio_format, job.bitrate, self._out_dir)
        try:
            result = self._downloader(request, on_progress)
        except Exception as exc:  # yt-dlp ném nhiều loại lỗi khác nhau
            logger.exception("Tải thất bại: %s", job.url)
            remove_partial_files(request)
            self.store.update(job_id, status=Status.ERROR, error=friendly_error(exc))
            return

        updated = self.store.update(
            job_id,
            status=Status.DONE,
            progress=100.0,
            title=result.title,
            duration=result.duration,
            file_path=str(result.file_path),
        )
        if updated is None:  # bị xoá trong lúc tải
            _remove_file(str(result.file_path))

    def delete(self, job_id: str) -> bool:
        job = self.store.remove(job_id)
        if job is None:
            return False
        _remove_file(job.file_path)
        return True

    def retry(self, job_id: str) -> Job | None:
        updated = self.store.update_if(
            job_id,
            lambda job: job.status is Status.ERROR,
            status=Status.QUEUED,
            progress=0.0,
            error=None,
        )
        if updated is not None:
            self._executor.submit(self._run, job_id)
        return updated

    def purge_orphans(self) -> int:
        return purge_generated_files(self._out_dir)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


class MergeService:
    def __init__(
        self,
        downloads: DownloadService,
        out_dir: Path,
        merger: Merger = merge_audio,
    ) -> None:
        self.store: Store[Merge] = Store()
        self._downloads = downloads
        self._out_dir = out_dir
        self._merger = merger
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="merge")

    def _source_files(self, job_ids: list[str]) -> list[Path]:
        files: list[Path] = []
        for job_id in job_ids:
            job = self._downloads.store.get(job_id)
            if job is None:
                raise MergeValidationError(f"Không tìm thấy audio {job_id}")
            if job.status is not Status.DONE or not job.file_path:
                raise MergeValidationError(f"Audio '{job.title or job.url}' chưa xử lý xong")
            files.append(Path(job.file_path))
        return files

    def submit(self, job_ids: list[str], name: str, audio_format: str, bitrate: int, gap_seconds: float) -> Merge:
        if len(job_ids) < 2:
            raise MergeValidationError("Chọn ít nhất 2 đoạn audio để gộp")
        self._source_files(job_ids)
        merge_id = new_id()
        merge = Merge(
            id=merge_id,
            name=safe_filename(name, f"gop-{merge_id}"),
            job_ids=tuple(job_ids),
            audio_format=audio_format,
            bitrate=bitrate,
            gap_seconds=gap_seconds,
        )
        self.store.add(merge)
        self._executor.submit(self._run, merge_id)
        return merge

    def _run(self, merge_id: str) -> None:
        merge = self.store.update(merge_id, status=Status.MERGING)
        if merge is None:
            return
        output = self._out_dir / f"{merge.id}.{merge.audio_format}"
        try:
            inputs = self._source_files(list(merge.job_ids))
            self._merger(inputs, output, merge.audio_format, merge.bitrate, merge.gap_seconds)
        except Exception as exc:
            logger.exception("Gộp thất bại: %s", merge_id)
            _remove_file(str(output))
            # Chỉ trả về thông báo do hệ thống tự soạn; chi tiết (đường dẫn, stderr) chỉ ghi log.
            safe = isinstance(exc, (MergeFailed, MergeValidationError))
            self.store.update(merge_id, status=Status.ERROR, error=str(exc) if safe else "Gộp thất bại")
            return
        if self.store.update(merge_id, status=Status.DONE, file_path=str(output)) is None:
            _remove_file(str(output))

    def delete(self, merge_id: str) -> bool:
        merge = self.store.remove(merge_id)
        if merge is None:
            return False
        _remove_file(merge.file_path)
        return True

    def purge_orphans(self) -> int:
        return purge_generated_files(self._out_dir)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
