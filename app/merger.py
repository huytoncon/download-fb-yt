"""Gộp nhiều file audio thành một file bằng ffmpeg (có thể chèn khoảng lặng giữa các đoạn)."""

import logging
import subprocess
from pathlib import Path

from app.config import AUDIO_CODECS, MERGE_TIMEOUT_SECONDS, SAMPLE_RATE
from app.ffmpeg import get_ffmpeg_path

logger = logging.getLogger(__name__)
_STDERR_TAIL = 500


class MergeFailed(Exception):
    pass


def _codec_args(audio_format: str, bitrate: int) -> list[str]:
    codec = AUDIO_CODECS[audio_format]
    args = ["-c:a", codec]
    if audio_format != "wav":
        args += ["-b:a", f"{bitrate}k"]
    return args


def build_merge_command(
    ffmpeg: str,
    inputs: list[Path],
    output: Path,
    audio_format: str,
    bitrate: int,
    gap_seconds: float = 0.0,
) -> list[str]:
    if len(inputs) < 2:
        raise ValueError("Cần ít nhất 2 file để gộp")

    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    for path in inputs:
        cmd += ["-i", str(path)]

    gap_count = len(inputs) - 1 if gap_seconds > 0 else 0
    for _ in range(gap_count):
        cmd += ["-f", "lavfi", "-t", f"{gap_seconds:g}", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo"]

    # Chuẩn hoá mọi đoạn về cùng sample rate / kênh để concat không lỗi.
    normalize = f"aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo"
    filters = [f"[{i}:a]{normalize}[a{i}]" for i in range(len(inputs))]
    order: list[str] = []
    for i in range(len(inputs)):
        order.append(f"[a{i}]")
        if i < gap_count:
            gap_index = len(inputs) + i
            filters.append(f"[{gap_index}:a]{normalize}[g{i}]")
            order.append(f"[g{i}]")
    filters.append(f"{''.join(order)}concat=n={len(order)}:v=0:a=1[out]")

    cmd += ["-filter_complex", ";".join(filters), "-map", "[out]", "-vn"]
    cmd += _codec_args(audio_format, bitrate)
    cmd.append(str(output))
    return cmd


def merge_audio(
    inputs: list[Path],
    output: Path,
    audio_format: str,
    bitrate: int,
    gap_seconds: float = 0.0,
) -> Path:
    missing = [p for p in inputs if not p.exists()]
    if missing:
        logger.error("Thiếu file nguồn khi gộp: %s", missing)
        raise MergeFailed(f"Thiếu {len(missing)} file nguồn (có thể đã bị xoá)")
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_merge_command(get_ffmpeg_path(), inputs, output, audio_format, bitrate, gap_seconds)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=MERGE_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        output.unlink(missing_ok=True)
        raise MergeFailed("Gộp quá thời gian cho phép") from None
    if result.returncode != 0 or not output.exists():
        output.unlink(missing_ok=True)
        logger.error("ffmpeg lỗi (%s): %s", result.returncode, result.stderr.strip()[-_STDERR_TAIL:])
        raise MergeFailed("ffmpeg không gộp được các file này")
    return output
