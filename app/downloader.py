"""Tải audio từ một link YouTube/Facebook bằng yt-dlp rồi chuyển sang định dạng mong muốn."""

import re
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from yt_dlp import YoutubeDL
from yt_dlp.utils import match_filter_func

from app.config import (
    COOKIES_FILE,
    MAX_DURATION_SECONDS,
    MAX_FILESIZE_BYTES,
    SOCKET_TIMEOUT_SECONDS,
)
from app.ffmpeg import get_ffmpeg_path

# Chỉ các extractor cho MỘT video. Loại bỏ playlist/kênh (youtube:tab...) và Generic
# (tránh yt-dlp đi theo redirect tới địa chỉ tuỳ ý — SSRF).
ALLOWED_EXTRACTORS = ["youtube", "youtubeytbe", "facebook", "facebook:reel", "facebookpluginsvideo"]

# (phần trăm, giai đoạn) — giai đoạn là "downloading" hoặc "converting"
ProgressCallback = Callable[[float, str], None]

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_MAX_ERROR_LENGTH = 300


class DownloadFailed(Exception):
    pass


@dataclass(frozen=True)
class DownloadRequest:
    job_id: str
    url: str
    audio_format: str
    bitrate: int
    out_dir: Path


@dataclass(frozen=True)
class DownloadResult:
    file_path: Path
    title: str
    duration: float | None


def _js_runtimes() -> dict:
    """YouTube cần một JS runtime để giải mã chữ ký; dùng deno/node nếu có."""
    runtimes = {name: {} for name in ("deno", "node") if shutil.which(name)}
    return runtimes or {"deno": {}}


def _progress_hook(on_progress: ProgressCallback) -> Callable[[dict], None]:
    def hook(event: dict) -> None:
        if event.get("status") == "downloading":
            total = event.get("total_bytes") or event.get("total_bytes_estimate")
            if total:
                percent = event.get("downloaded_bytes", 0) * 100 / total
                on_progress(min(percent, 99.0), "downloading")
        elif event.get("status") == "finished":
            on_progress(99.0, "converting")

    return hook


def build_options(request: DownloadRequest, on_progress: ProgressCallback, cookie_file: str | None = None) -> dict:
    options = {
        "format": "bestaudio/best",
        "outtmpl": str(request.out_dir / f"{request.job_id}.%(ext)s"),
        "noplaylist": True,
        "playlist_items": "1",
        "allowed_extractors": ALLOWED_EXTRACTORS,
        "match_filter": match_filter_func(f"!is_live & duration <=? {MAX_DURATION_SECONDS}"),
        "max_filesize": MAX_FILESIZE_BYTES,
        "socket_timeout": SOCKET_TIMEOUT_SECONDS,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "ffmpeg_location": get_ffmpeg_path(),
        "js_runtimes": _js_runtimes(),
        "progress_hooks": [_progress_hook(on_progress)],
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": request.audio_format,
                "preferredquality": str(request.bitrate),
            }
        ],
    }
    if cookie_file:
        options["cookiefile"] = cookie_file
    return options


@contextmanager
def _job_cookie_file() -> Iterator[str | None]:
    """yt-dlp ghi đè cookiefile khi đóng; mỗi job dùng bản sao riêng để các luồng không ghi đè lẫn nhau."""
    if not COOKIES_FILE:
        yield None
        return
    handle, path = tempfile.mkstemp(prefix="l2a-cookies-", suffix=".txt")
    try:
        with open(handle, "wb") as target, open(COOKIES_FILE, "rb") as source:
            shutil.copyfileobj(source, target)
        yield path
    finally:
        Path(path).unlink(missing_ok=True)


def remove_partial_files(request: DownloadRequest) -> None:
    for leftover in request.out_dir.glob(f"{request.job_id}.*"):
        leftover.unlink(missing_ok=True)


def friendly_error(exc: Exception) -> str:
    message = _ANSI.sub("", str(exc)).replace("ERROR: ", "").strip()
    lowered = message.lower()
    if "private" in lowered or "login" in lowered or "sign in" in lowered:
        message = "Video riêng tư hoặc cần đăng nhập (hãy cấu hình L2A_COOKIES_FILE). " + message
    elif "unsupported url" in lowered or "no suitable extractor" in lowered:
        message = "Link không được hỗ trợ (chỉ nhận link một video, không nhận playlist/kênh). " + message
    elif "does not pass filter" in lowered:
        message = f"Bỏ qua: video đang livestream hoặc dài hơn {MAX_DURATION_SECONDS // 3600} giờ."
    elif "file is larger than max-filesize" in lowered:
        message = "File quá lớn (giới hạn 1 GB)."
    return message[:_MAX_ERROR_LENGTH] or "Lỗi không xác định"


def download_audio(request: DownloadRequest, on_progress: ProgressCallback) -> DownloadResult:
    request.out_dir.mkdir(parents=True, exist_ok=True)
    with _job_cookie_file() as cookie_file:
        with YoutubeDL(build_options(request, on_progress, cookie_file)) as ydl:
            info = ydl.extract_info(request.url, download=True)
    if info and info.get("entries"):
        info = next((e for e in info["entries"] if e), {})
    file_path = request.out_dir / f"{request.job_id}.{request.audio_format}"
    if not file_path.exists():
        raise DownloadFailed(
            "Không tạo được file audio từ link này "
            f"(video có thể đang livestream, dài hơn {MAX_DURATION_SECONDS // 3600} giờ hoặc lớn hơn 1 GB)"
        )
    return DownloadResult(
        file_path=file_path,
        title=(info or {}).get("title") or request.job_id,
        duration=(info or {}).get("duration"),
    )
