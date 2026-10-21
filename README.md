# Link2Audio

Dán link YouTube / Facebook (nhiều link cùng lúc) → hệ thống tải và chuyển thành file audio → chọn các đoạn để gộp thành một file.

## Chạy

Windows: bấm đúp `run.bat` (lần đầu tự tạo môi trường và cài thư viện), trình duyệt mở http://127.0.0.1:8000.

Thủ công:

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --port 8000
```

Không cần cài ffmpeg: nếu máy chưa có, dùng bản đóng gói trong `imageio-ffmpeg`.
YouTube cần một JS runtime (Node.js hoặc Deno) để yt-dlp giải mã — hệ thống tự dùng nếu có trong PATH.

## Quy trình

1. **Dán link** — mỗi dòng một link, hoặc cách nhau bởi dấu cách / phẩy. Link trùng tự bỏ, link không phải YouTube/Facebook bị báo lại. Tối đa 50 link mỗi lần. Chọn định dạng (MP3 / M4A / WAV) và chất lượng (128–320 kbps). `Ctrl+Enter` để gửi.
2. **Xử lý** — mỗi link là một job: `Đang chờ → Đang tải (%) → Đang chuyển đổi → Hoàn tất / Lỗi`. Chạy song song 3 job (đổi bằng `L2A_WORKERS`). Nghe thử, tải về, thử lại job lỗi, xoá.
3. **Gộp** — tích chọn các đoạn đã xong (thứ tự tích = thứ tự gộp, chỉnh bằng ↑ ↓), đặt tên, khoảng lặng giữa các đoạn (0–10s), định dạng đầu ra. Các nguồn khác sample rate / số kênh được chuẩn hoá về 44.1 kHz stereo trước khi nối.

## Cấu hình (biến môi trường)

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `L2A_DATA_DIR` | `./data` | Nơi lưu file tải về (`downloads/`) và file gộp (`merged/`) |
| `L2A_WORKERS` | `3` | Số link xử lý song song |
| `L2A_COOKIES_FILE` | — | File cookies (định dạng Netscape) cho video Facebook/YouTube cần đăng nhập |
| `L2A_CORS_ORIGINS` | — | Origin được gọi API từ trình duyệt, vd `https://ten-ban.github.io` (nhiều origin cách nhau dấu phẩy) |
| `L2A_API_KEY` | — | Nếu đặt, mọi request `/api/*` phải có header `X-API-Key` (hoặc `?key=`) |

## Deploy

GitHub Pages chỉ chạy web tĩnh, còn việc tải YouTube/Facebook và ffmpeg phải chạy trên server. Vì vậy hệ thống chia làm hai phần:

```
[GitHub Pages: frontend/]  ──fetch + X-API-Key──▶  [Backend FastAPI: yt-dlp + ffmpeg]
  https://ten-ban.github.io/link2audio              máy bạn / Render / Railway / VPS
```

### 1. Frontend lên GitHub Pages

1. Tạo repo trên GitHub rồi push code:
   ```bash
   git remote add origin https://github.com/<ten-ban>/link2audio.git
   git push -u origin main
   ```
2. Repo → **Settings → Pages → Source: GitHub Actions**.
3. (Tuỳ chọn) **Settings → Secrets and variables → Actions → Variables**: tạo `L2A_API_BASE` = địa chỉ backend. Nếu bỏ qua, người dùng tự nhập trong mục **Máy chủ** trên giao diện.
4. Workflow [.github/workflows/pages.yml](.github/workflows/pages.yml) tự deploy mỗi khi `frontend/` thay đổi. Workflow [ci.yml](.github/workflows/ci.yml) chạy test.

### 2. Backend

**Chạy trên máy bạn** (đơn giản nhất, IP nhà ít bị YouTube chặn):

```bat
set L2A_CORS_ORIGINS=https://ten-ban.github.io
set L2A_API_KEY=mot-chuoi-bi-mat-dai
run.bat
```

Mở trang GitHub Pages → **Máy chủ** → nhập `http://127.0.0.1:8000` và API key. Trình duyệt cho phép trang HTTPS gọi `http://127.0.0.1` (Chrome có thể hỏi quyền truy cập mạng cục bộ — chọn Cho phép).

**Chạy trên cloud** bằng Docker (Render, Railway, Fly.io, VPS):

```bash
docker build -t link2audio .
docker run -p 8000:8000 -e L2A_CORS_ORIGINS=https://ten-ban.github.io -e L2A_API_KEY=... link2audio
```

Backend công khai **bắt buộc** đặt `L2A_API_KEY`, nếu không ai cũng dùng được server của bạn. YouTube hay chặn IP datacenter ("Sign in to confirm you're not a bot") — khi đó cần `L2A_COOKIES_FILE`. Ổ đĩa trên các gói miễn phí thường bị xoá khi khởi động lại.

## API

Mọi response có dạng `{"success": bool, "data": ..., "error": str | null}`.

| Method | Path | Mô tả |
|---|---|---|
| POST | `/api/jobs` | `{links, audio_format, bitrate}` — tạo job từ đoạn text chứa nhiều link |
| GET | `/api/jobs` | Danh sách job và tiến độ |
| POST | `/api/jobs/{id}/retry` | Chạy lại job lỗi |
| DELETE | `/api/jobs/{id}` | Xoá job và file |
| GET | `/api/jobs/{id}/file` | Tải file audio |
| POST | `/api/merges` | `{job_ids, name, gap_seconds, audio_format, bitrate}` — gộp |
| GET | `/api/merges` | Danh sách file gộp |
| DELETE | `/api/merges/{id}` | Xoá file gộp |
| GET | `/api/merges/{id}/file` | Tải file gộp |

## Cấu trúc

```
app/
  links.py       tách & kiểm tra link (chỉ chấp nhận domain YouTube/Facebook)
  downloader.py  yt-dlp: tải audio, báo tiến độ, chuyển định dạng
  merger.py      ffmpeg: nối nhiều file, chèn khoảng lặng
  services.py    hàng đợi tải song song + hàng đợi gộp
  store.py       trạng thái job trong bộ nhớ (bất biến, an toàn đa luồng)
  main.py        FastAPI (CORS, API key)
frontend/        giao diện web tĩnh (deploy GitHub Pages)
tests/           pytest
.github/         workflow deploy Pages + CI
```

## Test

```bash
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest --cov=app
```

## Lưu ý

- Danh sách job lưu trong bộ nhớ: khởi động lại server thì danh sách mất (file trong `data/` vẫn còn).
- `run.bat` chỉ nghe `127.0.0.1`. Khi mở backend ra internet: đặt `L2A_API_KEY`, dùng HTTPS, và cân nhắc thêm giới hạn tần suất ở reverse proxy.
- Chỉ tải nội dung bạn có quyền sử dụng, tuân thủ điều khoản của YouTube/Facebook.
- Khi YouTube/Facebook thay đổi, cập nhật yt-dlp: `.venv\Scripts\pip install -U "yt-dlp[default]"`.
