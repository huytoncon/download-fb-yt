"""Cấu hình chung, có thể ghi đè bằng biến môi trường."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("L2A_DATA_DIR", BASE_DIR / "data"))
DOWNLOAD_DIR = DATA_DIR / "downloads"
MERGED_DIR = DATA_DIR / "merged"
FRONTEND_DIR = BASE_DIR / "frontend"

# Origin được phép gọi API từ trình duyệt, vd "https://ten-ban.github.io" (phân tách bằng dấu phẩy).
CORS_ORIGINS = [o.strip() for o in os.environ.get("L2A_CORS_ORIGINS", "").split(",") if o.strip()]
# Nếu đặt, mọi request /api phải gửi header X-API-Key (hoặc ?key=) trùng giá trị này.
API_KEY = os.environ.get("L2A_API_KEY") or None

MAX_LINKS_PER_REQUEST = 50
MAX_MERGE_ITEMS = 100
MAX_GAP_SECONDS = 10
MAX_WORKERS = int(os.environ.get("L2A_WORKERS", "3"))
MAX_PENDING_JOBS = 200  # tổng số job đang chờ/đang chạy trên toàn server
MAX_PENDING_PER_CLIENT = 30  # mỗi người dùng (trình duyệt) tối đa bấy nhiêu link chưa xong
# Job/file gộp tự xoá sau khoảng thời gian này để server nhiều người dùng không đầy ổ đĩa.
ITEM_TTL_SECONDS = int(float(os.environ.get("L2A_TTL_HOURS", "6")) * 3600)
SWEEP_INTERVAL_SECONDS = 600
MAX_DURATION_SECONDS = int(os.environ.get("L2A_MAX_DURATION", str(4 * 3600)))
MAX_FILESIZE_BYTES = 1024 * 1024 * 1024
SOCKET_TIMEOUT_SECONDS = 30
MERGE_TIMEOUT_SECONDS = 3600

# File cookies (định dạng Netscape) để tải video Facebook/YouTube cần đăng nhập.
COOKIES_FILE = os.environ.get("L2A_COOKIES_FILE") or None

AUDIO_CODECS = {
    "mp3": "libmp3lame",
    "m4a": "aac",
    "wav": "pcm_s16le",
}
ALLOWED_BITRATES = (128, 192, 256, 320)
SAMPLE_RATE = 44100
