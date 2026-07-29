"""Internal-only wrapper around the official MinerU local CLI."""

import asyncio
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
parse_lock = asyncio.Lock()
MAX_FILE_BYTES = 100 * 1024 * 1024
SUPPORTED_SUFFIXES = {".pdf", ".docx", ".pptx", ".xlsx"}
SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")
# Reuse Uvicorn's configured logger so CLI lifecycle records are visible from
# `docker compose logs mineru` without custom logging setup.
logger = logging.getLogger("uvicorn.error")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "backend": os.getenv("MINERU_BACKEND", "pipeline")}


def safe_filename(name: str) -> str:
    cleaned = SAFE_FILENAME_RE.sub("_", Path(name or "upload.bin").name).strip("._")
    return cleaned or "upload.bin"


def output_tail(value: object, limit: int = 1600) -> str:
    """Keep an actionable local CLI error tail without dumping a whole file."""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    text = " ".join(str(value or "").split())
    return text[-limit:] or "(no process output)"


def positive_int_from_environment(name: str, default: int) -> int:
    """Read a positive integer setting without crashing the parser service."""
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using %s", name, value, default)
        return default
    if parsed <= 0:
        logger.warning("Ignoring non-positive %s=%r; using %s", name, value, default)
        return default
    return parsed


def run_mineru(source_path: Path) -> str:
    output_directory = source_path.parent / "output"
    backend = os.getenv("MINERU_BACKEND", "pipeline").strip() or "pipeline"
    timeout_seconds = positive_int_from_environment("MINERU_PARSE_TIMEOUT_SECONDS", 900)
    environment = os.environ.copy()
    environment.setdefault("MINERU_MODEL_SOURCE", "modelscope")
    started_at = time.monotonic()
    logger.info(
        "MinerU CLI started: file=%s size_bytes=%s backend=%s timeout_seconds=%s",
        source_path.name,
        source_path.stat().st_size,
        backend,
        timeout_seconds,
    )
    try:
        result = subprocess.run(
            ["mineru", "-p", str(source_path), "-o", str(output_directory), "-b", backend],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        logger.error(
            "MinerU CLI timed out: file=%s backend=%s timeout_seconds=%s elapsed_seconds=%.1f "
            "stderr_tail=%r",
            source_path.name,
            backend,
            timeout_seconds,
            time.monotonic() - started_at,
            output_tail(exc.stderr),
        )
        raise HTTPException(504, f"MinerU 解析超过 {timeout_seconds} 秒，请拆分文件后重试。") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "未知错误").strip().replace("\n", " ")
        # The actual MinerU exception is printed at the end, after startup logs.
        detail = detail[-1800:]
        logger.error(
            "MinerU CLI failed: file=%s backend=%s return_code=%s elapsed_seconds=%.1f "
            "stderr_tail=%r",
            source_path.name,
            backend,
            result.returncode,
            time.monotonic() - started_at,
            output_tail(result.stderr),
        )
        raise HTTPException(422, f"MinerU 解析失败：{detail}")

    markdown_files = sorted(output_directory.rglob("*.md"), key=lambda item: item.stat().st_size, reverse=True)
    if not markdown_files:
        raise HTTPException(422, "MinerU 未生成 Markdown 解析结果。")
    markdown = markdown_files[0].read_text(encoding="utf-8", errors="replace").strip()
    if not markdown:
        raise HTTPException(422, "MinerU 未提取到可用文字。")
    logger.info(
        "MinerU CLI completed: file=%s backend=%s elapsed_seconds=%.1f markdown_characters=%s",
        source_path.name,
        backend,
        time.monotonic() - started_at,
        len(markdown),
    )
    return markdown


@app.post("/parse")
async def parse(file: UploadFile = File(...)) -> dict[str, str]:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(415, "MinerU 当前仅处理 PDF、DOCX、PPTX、XLSX；请由 Docling 处理其他格式。")

    content = await file.read()
    if not content:
        raise HTTPException(422, "上传文件为空。")
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(413, "单个文件超过 MinerU 100 MB 解析上限，请拆分后重试。")

    queued_at = time.monotonic()
    file_name = safe_filename(file.filename or f"upload{suffix}")
    logger.info(
        "MinerU parse request received: file=%s size_bytes=%s",
        file_name,
        len(content),
    )
    async with parse_lock:
        logger.info(
            "MinerU parse slot acquired: file=%s queue_wait_seconds=%.1f",
            file_name,
            time.monotonic() - queued_at,
        )
        with tempfile.TemporaryDirectory(prefix="mineru-") as temporary_directory:
            source_path = Path(temporary_directory) / file_name
            source_path.write_bytes(content)
            markdown = await asyncio.to_thread(run_mineru, source_path)
    return {"markdown": markdown}
