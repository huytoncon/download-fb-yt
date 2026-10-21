"""Hàng đợi xử lý: tải nhiều link song song và gộp audio chạy nền."""

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from app.config import MAX_PENDING_JOBS, MAX_PENDING_PER_CLIENT

MAX_PENDING_MERGES_PER_CLIENT = 2
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


def _expire(store: Store, max_age_seconds: float) -> int:
    """Xoá các mục đã xong/lỗi quá hạn cùng file của chúng; trả về số mục đã xoá."""
    cutoff = time.time() - max_age_seconds
    expired = store.list_where(lambda item: item.status.is_finished and item.created_at < cutoff)
    for item in expired:
        if store.remove_if(item.id, lambda current: current.status.is_finished) is not None:
            _remove_file(item.file_path)
    return len(expired)


class DownloadService:
    def __init__(
        self,
        out_dir: Path,
        max_workers: int,
        downloader: Downloader = download_audio,
        max_pending: int = MAX_PENDING_JOBS,
        max_pending_per_owner: int = MAX_PENDING_PER_CLIENT,
    ) -> None:
        self.store: Store[Job] = Store()
        self._out_dir = out_dir
        self._downloader = downloader
        self._max_pending = max_pending
        self._max_pending_per_owner = max_pending_per_owner
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="download")

    def submit(self, links: list[tuple[str, str]], audio_format: str, bitrate: int, owner: str) -> list[Job]:
        if self.store.count(_is_pending) + len(links) > self._max_pending:
            raise QueueFullError("Máy chủ đang bận (hàng đợi đầy), thử lại sau ít phút")
        owner_pending = self.store.count(lambda j: j.owner == owner and _is_pending(j))
        if owner_pending + len(links) > self._max_pending_per_owner:
            raise QueueFullError(
                f"Bạn đang có {owner_pending} link chưa xong (tối đa {self._max_pending_per_owner}), chờ xong rồi thêm tiếp"
            )
        jobs = [
            self.store.add(
                Job(id=new_id(), url=url, source=source, audio_format=audio_format, bitrate=bitrate, owner=owner)
            )
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

    def list_for(self, owner: str) -> list[Job]:
        return self.store.list_where(lambda j: j.owner == owner)

    def get_for(self, job_id: str, owner: str) -> Job | None:
        job = self.store.get(job_id)
        return job if job is not None and job.owner == owner else None

    def delete(self, job_id: str, owner: str) -> bool:
        job = self.store.remove_if(job_id, lambda j: j.owner == owner)
        if job is None:
            return False
        _remove_file(job.file_path)
        return True

    def expire(self, max_age_seconds: float) -> int:
        return _expire(self.store, max_age_seconds)

    def retry(self, job_id: str, owner: str) -> Job | None:
        updated = self.store.update_if(
            job_id,
            lambda job: job.owner == owner and job.status is Status.ERROR,
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

    def _source_files(self, job_ids: list[str], owner: str) -> list[Path]:
        files: list[Path] = []
        for job_id in job_ids:
            job = self._downloads.get_for(job_id, owner)
            if job is None:
                raise MergeValidationError(f"Không tìm thấy audio {job_id}")
            if job.status is not Status.DONE or not job.file_path:
                raise MergeValidationError(f"Audio '{job.title or job.url}' chưa xử lý xong")
            files.append(Path(job.file_path))
        return files

    def submit(
        self, job_ids: list[str], name: str, audio_format: str, bitrate: int, gap_seconds: float, owner: str
    ) -> Merge:
        if len(job_ids) < 2:
            raise MergeValidationError("Chọn ít nhất 2 đoạn audio để gộp")
        if self.store.count(lambda m: m.owner == owner and not m.status.is_finished) >= MAX_PENDING_MERGES_PER_CLIENT:
            raise MergeValidationError("Bạn đang có bản gộp chưa xong, chờ xong rồi gộp tiếp")
        self._source_files(job_ids, owner)
        merge_id = new_id()
        merge = Merge(
            id=merge_id,
            name=safe_filename(name, f"gop-{merge_id}"),
            job_ids=tuple(job_ids),
            audio_format=audio_format,
            bitrate=bitrate,
            gap_seconds=gap_seconds,
            owner=owner,
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
            inputs = self._source_files(list(merge.job_ids), merge.owner)
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

    def list_for(self, owner: str) -> list[Merge]:
        return self.store.list_where(lambda m: m.owner == owner)

    def get_for(self, merge_id: str, owner: str) -> Merge | None:
        merge = self.store.get(merge_id)
        return merge if merge is not None and merge.owner == owner else None

    def delete(self, merge_id: str, owner: str) -> bool:
        merge = self.store.remove_if(merge_id, lambda m: m.owner == owner)
        if merge is None:
            return False
        _remove_file(merge.file_path)
        return True

    def expire(self, max_age_seconds: float) -> int:
        return _expire(self.store, max_age_seconds)

    def purge_orphans(self) -> int:
        return purge_generated_files(self._out_dir)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
