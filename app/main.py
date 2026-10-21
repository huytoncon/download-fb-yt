"""API + giao diện web cho hệ thống chuyển link YouTube/Facebook thành audio."""

import asyncio
import hmac
import logging
import re
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import config
from app.links import parse_links
from app.services import DownloadService, MergeService, MergeValidationError, QueueFullError, safe_filename
from app.store import Status

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

AudioFormat = Literal["mp3", "m4a", "wav"]
Bitrate = Literal[128, 192, 256, 320]


class CreateJobsRequest(BaseModel):
    links: str = Field(min_length=1, max_length=50_000)
    audio_format: AudioFormat = "mp3"
    bitrate: Bitrate = 192


class MergeRequest(BaseModel):
    job_ids: list[str] = Field(min_length=2, max_length=config.MAX_MERGE_ITEMS)
    name: str = Field(default="", max_length=120)
    audio_format: AudioFormat = "mp3"
    bitrate: Bitrate = 192
    gap_seconds: float = Field(default=0.0, ge=0.0, le=config.MAX_GAP_SECONDS)


def ok(data) -> dict:
    return {"success": True, "data": data, "error": None}


def fail(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"success": False, "data": None, "error": message})


def _file_response(file_path: str | None, download_name: str, audio_format: str) -> FileResponse:
    if not file_path or not Path(file_path).exists():
        raise HTTPException(status_code=404, detail="File không tồn tại")
    return FileResponse(file_path, filename=f"{download_name}.{audio_format}")


# <audio>/<a> không gửi được header, nên riêng route tải file được nhận key/client qua query.
_FILE_ROUTE = re.compile(r"^/api/(jobs|merges)/[^/]+/file$")
_CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def _is_file_download(request: Request) -> bool:
    return request.method == "GET" and bool(_FILE_ROUTE.match(request.url.path))


def _key_matches(request: Request, api_key: str) -> bool:
    provided = request.headers.get("x-api-key") or ""
    if not provided and _is_file_download(request):
        provided = request.query_params.get("key") or ""
    return hmac.compare_digest(provided.encode(), api_key.encode())


def client_id(request: Request) -> str:
    """Mỗi trình duyệt tự sinh một mã ngẫu nhiên; mỗi người chỉ thấy job/bản gộp của mình."""
    value = request.headers.get("x-client-id") or ""
    if not value and _is_file_download(request):
        value = request.query_params.get("client") or ""
    if not _CLIENT_ID.match(value):
        raise HTTPException(status_code=400, detail="Thiếu hoặc sai mã phiên trình duyệt (X-Client-Id)")
    return value


Client = Annotated[str, Depends(client_id)]


async def _sweep_forever(downloads: DownloadService, merges: MergeService, ttl_seconds: float) -> None:
    while True:
        await asyncio.sleep(config.SWEEP_INTERVAL_SECONDS)
        try:
            removed = await run_in_threadpool(lambda: downloads.expire(ttl_seconds) + merges.expire(ttl_seconds))
            if removed:
                logger.info("Đã xoá %d mục hết hạn", removed)
        except Exception:
            logger.exception("Dọn dữ liệu hết hạn thất bại")


def create_app(
    downloads: DownloadService | None = None,
    merges: MergeService | None = None,
    cors_origins: list[str] | None = None,
    api_key: str | None = None,
) -> FastAPI:
    cors_origins = config.CORS_ORIGINS if cors_origins is None else cors_origins
    api_key = config.API_KEY if api_key is None else api_key
    if cors_origins and not api_key:
        raise RuntimeError("Đã bật L2A_CORS_ORIGINS (truy cập từ web khác) thì bắt buộc đặt L2A_API_KEY")
    downloads = downloads or DownloadService(config.DOWNLOAD_DIR, config.MAX_WORKERS)
    merges = merges or MergeService(downloads, config.MERGED_DIR)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        removed = downloads.purge_orphans() + merges.purge_orphans()
        if removed:
            logger.info("Đã dọn %d file còn sót từ lần chạy trước", removed)
        sweeper = asyncio.create_task(_sweep_forever(downloads, merges, config.ITEM_TTL_SECONDS))
        yield
        sweeper.cancel()
        with suppress(asyncio.CancelledError):
            await sweeper
        downloads.shutdown()
        merges.shutdown()

    app = FastAPI(title="Link2Audio", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def require_api_key(request: Request, call_next):
        is_api = request.url.path.startswith("/api/")
        if api_key and is_api and request.method != "OPTIONS" and not _key_matches(request, api_key):
            return fail(401, "Sai hoặc thiếu API key")
        return await call_next(request)

    # Thêm sau cùng để CORS bọc ngoài cùng (xử lý preflight và gắn header cho cả response 401).
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Content-Type", "X-API-Key", "X-Client-Id"],
            expose_headers=["Content-Disposition"],
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_, exc: StarletteHTTPException):
        return fail(exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_, exc: RequestValidationError):
        fields = ", ".join(".".join(str(p) for p in err["loc"][1:]) for err in exc.errors())
        return fail(422, f"Dữ liệu không hợp lệ: {fields}")

    @app.get("/api/health")
    def health():
        return ok({"status": "ok", "auth": bool(api_key), "ttl_hours": config.ITEM_TTL_SECONDS / 3600})

    @app.post("/api/jobs")
    def create_jobs(body: CreateJobsRequest, owner: Client):
        parsed = parse_links(body.links)
        if not parsed.valid:
            return fail(400, "Không có link YouTube/Facebook hợp lệ nào")
        if len(parsed.valid) > config.MAX_LINKS_PER_REQUEST:
            return fail(400, f"Tối đa {config.MAX_LINKS_PER_REQUEST} link mỗi lần")
        try:
            jobs = downloads.submit(list(parsed.valid), body.audio_format, body.bitrate, owner)
        except QueueFullError as exc:
            return fail(429, str(exc))
        return ok({"jobs": [j.to_public() for j in jobs], "invalid": list(parsed.invalid)})

    @app.get("/api/jobs")
    def list_jobs(owner: Client):
        return ok([j.to_public() for j in downloads.list_for(owner)])

    @app.post("/api/jobs/{job_id}/retry")
    def retry_job(job_id: str, owner: Client):
        job = downloads.retry(job_id, owner)
        if job is None:
            return fail(400, "Chỉ thử lại được audio bị lỗi")
        return ok(job.to_public())

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str, owner: Client):
        if not downloads.delete(job_id, owner):
            return fail(404, "Không tìm thấy audio")
        return ok({"id": job_id})

    @app.get("/api/jobs/{job_id}/file")
    def download_job_file(job_id: str, owner: Client):
        job = downloads.get_for(job_id, owner)
        if job is None or job.status is not Status.DONE:
            raise HTTPException(status_code=404, detail="Audio chưa sẵn sàng")
        return _file_response(job.file_path, safe_filename(job.title or "", job.id), job.audio_format)

    @app.post("/api/merges")
    def create_merge(body: MergeRequest, owner: Client):
        try:
            merge = merges.submit(
                body.job_ids, body.name, body.audio_format, body.bitrate, body.gap_seconds, owner
            )
        except MergeValidationError as exc:
            return fail(400, str(exc))
        return ok(merge.to_public())

    @app.get("/api/merges")
    def list_merges(owner: Client):
        return ok([m.to_public() for m in merges.list_for(owner)])

    @app.delete("/api/merges/{merge_id}")
    def delete_merge(merge_id: str, owner: Client):
        if not merges.delete(merge_id, owner):
            return fail(404, "Không tìm thấy file gộp")
        return ok({"id": merge_id})

    @app.get("/api/merges/{merge_id}/file")
    def download_merge_file(merge_id: str, owner: Client):
        merge = merges.get_for(merge_id, owner)
        if merge is None or merge.status is not Status.DONE:
            raise HTTPException(status_code=404, detail="File gộp chưa sẵn sàng")
        return _file_response(merge.file_path, merge.name, merge.audio_format)

    app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
    return app


app = create_app()
