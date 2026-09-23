from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

from .dify_client import DifyClient, DifyIntegrationError, ReviewOptions


REPORT_DIR = Path(os.getenv("REPORT_DIR", "/app/data/reports"))
REPORT_DIR.mkdir(parents=True, exist_ok=True)
MAX_FILE_BYTES = 30 * 1024 * 1024
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt"}
SAFE_JOB_ID = re.compile(r"^[a-f0-9]{32}$")

app = FastAPI(
    title="AI 合同审核本地网关",
    version="1.1.0",
    docs_url="/docs",
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

jobs: dict[str, dict[str, Any]] = {}
tasks: set[asyncio.Task] = set()


def _manifest_path(job_id: str) -> Path:
    return REPORT_DIR / job_id / "manifest.json"


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in job.items() if key != "source_path"}


def _save(job: dict[str, Any]) -> None:
    path = _manifest_path(job["job_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(_public_job(job), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temp.replace(path)


def _load(job_id: str) -> dict[str, Any] | None:
    if job_id in jobs:
        return jobs[job_id]
    path = _manifest_path(job_id)
    if not path.is_file():
        return None
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    jobs[job_id] = job
    return job


async def _execute(job_id: str, source_path: Path, options: ReviewOptions) -> None:
    job = jobs[job_id]
    client = DifyClient()
    try:
        job.update(status="running", progress=15, message="正在把合同安全传递给 Dify 测试工作流")
        _save(job)
        result = await client.run_review(
            source_path=source_path,
            original_name=job["original_name"],
            content_type=job["content_type"],
            options=options,
            local_user_id=job["local_user_id"],
            report_dir=REPORT_DIR / job_id,
        )
        job.update(status="running", progress=90, message="正在整理审核报告和下载文件")
        _save(job)
        public_base = os.getenv("GATEWAY_PUBLIC_URL", "http://127.0.0.1:8787").rstrip("/")
        download_urls = {
            kind: f"{public_base}/api/reports/{job_id}/{filename}"
            for kind, filename in result.get("downloads", {}).items()
        }
        result["downloads"] = download_urls
        job.update(
            status="completed",
            progress=100,
            message="审核完成",
            result=result,
            completed_at=int(time.time()),
        )
    except DifyIntegrationError as exc:
        job.update(status="failed", progress=100, message=str(exc), error_code="DIFY_ERROR")
    except Exception:
        job.update(
            status="failed",
            progress=100,
            message="本地审核服务发生异常，请查看服务状态后重试。",
            error_code="INTERNAL_ERROR",
        )
    finally:
        source_path.unlink(missing_ok=True)
        job.pop("source_path", None)
        _save(job)


@app.get("/", response_class=HTMLResponse)
async def root() -> str:
    mode = DifyClient().mode
    return (
        "<!doctype html><meta charset='utf-8'><title>AI 合同审核网关</title>"
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:760px;"
        "margin:64px auto;padding:0 24px;color:#162033}code{background:#eef2f8;padding:3px 6px;"
        "border-radius:5px}</style><h1>AI 合同审核本地网关</h1>"
        f"<p>服务运行正常。当前模式：<code>{mode}</code></p>"
        "<p>请从 <a href='http://127.0.0.1:3000'>Open WebUI</a> 进入聊天式合同审核。</p>"
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    client = DifyClient()
    return {
        "status": "ok",
        "mode": client.mode,
        "dify_configured": client.live_ready,
        "result_cache_enabled": False,
        "version": "1.1.0",
    }


@app.post("/api/reviews", status_code=202)
async def create_review(
    file: UploadFile = File(...),
    declared_doc_type: str = Form("自动识别"),
    industry: str = Form("通用"),
    jurisdiction: str = Form("中国大陆"),
    strictness: str = Form("标准"),
    focus_risk_types: str = Form("全部"),
    local_user_id: str = Form("local-user"),
) -> dict[str, Any]:
    original_name = Path(file.filename or "contract").name
    extension = Path(original_name).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(400, "仅支持 PDF、DOCX 和 TXT 文件。")

    job_id = uuid.uuid4().hex
    job_dir = REPORT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    source_path = job_dir / f"source{extension}"
    size = 0
    with source_path.open("wb") as target:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                target.close()
                source_path.unlink(missing_ok=True)
                raise HTTPException(413, "文件超过 30 MB，请压缩后重试。")
            target.write(chunk)
    if size == 0:
        source_path.unlink(missing_ok=True)
        raise HTTPException(400, "文件为空，请重新选择合同。")

    content_type = file.content_type or mimetypes.guess_type(original_name)[0] or "application/octet-stream"
    job = {
        "job_id": job_id,
        "status": "queued",
        "progress": 5,
        "message": "文件上传成功，等待开始审核",
        "created_at": int(time.time()),
        "original_name": original_name,
        "content_type": content_type,
        "local_user_id": local_user_id[:128],
        "source_path": str(source_path),
        "mode": DifyClient().mode,
    }
    jobs[job_id] = job
    _save(job)
    options = ReviewOptions(
        declared_doc_type=declared_doc_type[:80],
        industry=industry[:80],
        jurisdiction=jurisdiction[:80],
        strictness=strictness[:20],
        focus_risk_types=focus_risk_types[:500],
    )
    task = asyncio.create_task(_execute(job_id, source_path, options))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return _public_job(job)


@app.get("/api/reviews/{job_id}")
async def get_review(job_id: str) -> dict[str, Any]:
    if not SAFE_JOB_ID.fullmatch(job_id):
        raise HTTPException(404, "审核任务不存在。")
    job = _load(job_id)
    if not job:
        raise HTTPException(404, "审核任务不存在。")
    if job.get("status") in {"queued", "running"} and not job.get("source_path"):
        job.update(
            status="failed",
            progress=100,
            message="服务重启导致本次审核中断，请重新上传合同。",
            error_code="RESTART_INTERRUPTED",
        )
        _save(job)
    return _public_job(job)


@app.get("/api/reports/{job_id}/{filename}")
async def download_report(job_id: str, filename: str) -> FileResponse:
    if not SAFE_JOB_ID.fullmatch(job_id):
        raise HTTPException(404, "报告不存在。")
    safe_name = Path(filename).name
    if safe_name not in {"contract-review.docx", "contract-review.pdf"}:
        raise HTTPException(404, "报告不存在。")
    path = REPORT_DIR / job_id / safe_name
    if not path.is_file():
        raise HTTPException(404, "报告文件尚未生成。")
    media_type = (
        "application/pdf"
        if path.suffix == ".pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return FileResponse(
        path,
        media_type=media_type,
        filename=f"AI合同审核报告{path.suffix}",
    )
