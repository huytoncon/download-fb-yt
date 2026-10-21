import re
import subprocess
from pathlib import Path

import pytest

from app.ffmpeg import get_ffmpeg_path


def make_tone(path: Path, seconds: float, frequency: int = 440, sample_rate: int = 44100, channels: int = 2) -> Path:
    """Tạo một file audio sóng sin thật bằng ffmpeg để test."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            get_ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={seconds}:sample_rate={sample_rate}",
            "-ac", str(channels), str(path),
        ],
        check=True,
    )
    return path


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        [get_ffmpeg_path(), "-hide_banner", "-i", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", result.stderr)
    assert match, result.stderr
    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


@pytest.fixture
def tone():
    return make_tone


@pytest.fixture
def duration_of():
    return probe_duration
