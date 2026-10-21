"""Tìm ffmpeg: ưu tiên bản cài trên máy, nếu không có thì dùng bản đóng gói của imageio-ffmpeg."""

import shutil
from functools import lru_cache

import imageio_ffmpeg


@lru_cache(maxsize=1)
def get_ffmpeg_path() -> str:
    return shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()
