"""Local document conversion with MinerU first and Docling as the broad fallback."""

import asyncio
import json
import logging
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx


# Uvicorn already owns the container logging configuration.  Using its logger
# keeps parser lifecycle and failure records visible in `docker compose logs`.
logger = logging.getLogger("uvicorn.error")


@dataclass
class ParsedDocument:
    markdown: str
    metadata: dict[str, Any]
    parser: str
    warnings: list[str]


class MinerUServiceUnavailable(RuntimeError):
    """MinerU could not accept or finish work without risking host overload."""


class MinerUConversionFailed(RuntimeError):
    """MinerU finished a request but did not return a usable conversion."""


VISION_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

SUPPORTED_SUFFIXES = {
    ".pdf", ".docx", ".xlsx", ".xls", ".pptx", ".txt", ".md", ".markdown",
    ".html", ".htm", ".csv", ".json", ".xml",
}

# MinerU currently provides local parsing for PDF, image, DOCX, PPTX and XLSX.
# Images in this project are passed to the multimodal model instead of local OCR.
MINERU_SUPPORTED_SUFFIXES = {".pdf", ".docx", ".xlsx", ".pptx"}
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_GLYPH_CODE_RE = re.compile(r"G[0-9A-F]{2}", re.IGNORECASE)
_ESCAPED_RANGE_MARKER_RE = re.compile(r"\\~")
_DOCLING_CONVERTER_LOCK = threading.Lock()
_PADDLE_OCR_LOCK = threading.Lock()
# MinerU, Docling and PaddleOCR all use sizeable CPU model stacks.  Knowledge
# jobs are queued separately in main.py; this lock extends that protection to
# authorized research attachments that call this module directly.
_LOCAL_PARSE_PIPELINE_LOCK = threading.BoundedSemaphore(1)


def _positive_int_from_environment(name: str, default: int) -> int:
    """Read a positive integer setting without making a malformed env fatal."""
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


def _error_summary(error: Exception | None, limit: int = 160) -> str:
    if error is None:
        return ""
    detail = " ".join(str(error).split())
    return detail[:limit] or error.__class__.__name__


@lru_cache(maxsize=1)
def _docling_converter():
    """Construct one local Docling converter per API process.

    Model construction is expensive on a CPU-only intranet host. The parser
    serializes use of this cached converter below so model state is not shared
    concurrently between document jobs.
    """
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    artifacts_path = Path(os.getenv("DOCLING_ARTIFACTS_PATH", "/data/models/docling"))
    artifacts_path.mkdir(parents=True, exist_ok=True)
    document_timeout_seconds = _positive_int_from_environment(
        "DOCLING_DOCUMENT_TIMEOUT_SECONDS",
        600,
    )
    pipeline_options = PdfPipelineOptions(
        artifacts_path=artifacts_path,
        document_timeout=document_timeout_seconds,
    )

    logger.info(
        "Knowledge parser model initialization started: parser=docling artifacts_path=%s "
        "document_timeout_seconds=%s",
        artifacts_path,
        document_timeout_seconds,
    )
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
        }
    )


@lru_cache(maxsize=1)
def _paddle_ocr():
    """Construct one PaddleOCR instance per API process, lazily and locally."""
    from paddleocr import PaddleOCR

    logger.info("Knowledge parser model initialization started: parser=paddleocr")
    return PaddleOCR(lang="ch")


def _ensure_readable_markdown(markdown: str, parser_name: str) -> str:
    """Reject empty text and PDF font-map residue before it reaches the vector index."""
    # MinerU may export a range such as 0.6-0.8 as Markdown-escaped `\\~`.
    # It is a literal range delimiter, not formatting, so preserve the meaning
    # with the standard Chinese full-width range marker before indexing.
    content = _ESCAPED_RANGE_MARKER_RE.sub("～", (markdown or "")).strip()
    if not content:
        raise ValueError(f"{parser_name} 未提取到可读文字")

    glyph_codes = len(_GLYPH_CODE_RE.findall(content))
    chinese_characters = len(_CJK_RE.findall(content))
    if glyph_codes >= 30 and chinese_characters < 20:
        raise ValueError(f"{parser_name} 提取结果疑似 PDF 字体编码乱码，未得到可用中文正文")
    return content


def _mineru_convert(path: Path) -> ParsedDocument:
    """Use the internal MinerU sidecar, never an external parsing service."""
    base_url = os.getenv("MINERU_API_URL", "http://mineru:8001").rstrip("/")
    execution_timeout_seconds = _positive_int_from_environment("MINERU_PARSE_TIMEOUT_SECONDS", 900)
    request_timeout_seconds = _positive_int_from_environment(
        "MINERU_REQUEST_TIMEOUT_SECONDS",
        execution_timeout_seconds + 60,
    )
    # The MinerU worker needs time to return its own actionable 504 response.
    # If the outer HTTP timeout fires first, the API only sees a generic
    # ReadTimeout and starts a costly fallback while MinerU may still be working.
    if request_timeout_seconds <= execution_timeout_seconds:
        request_timeout_seconds = execution_timeout_seconds + 60
        logger.warning(
            "MINERU_REQUEST_TIMEOUT_SECONDS must exceed MINERU_PARSE_TIMEOUT_SECONDS; "
            "using %s seconds for this request",
            request_timeout_seconds,
        )

    started_at = time.monotonic()
    logger.info(
        "Knowledge parse stage started: parser=mineru file=%s execution_timeout_seconds=%s "
        "request_timeout_seconds=%s",
        path.name,
        execution_timeout_seconds,
        request_timeout_seconds,
    )
    try:
        with path.open("rb") as source_file:
            response = httpx.post(
                f"{base_url}/parse",
                files={"file": (path.name, source_file, "application/octet-stream")},
                timeout=httpx.Timeout(request_timeout_seconds, connect=10.0),
            )
    except httpx.TimeoutException as exc:
        elapsed_seconds = time.monotonic() - started_at
        logger.warning(
            "Knowledge parse stage timed out: parser=mineru file=%s elapsed_seconds=%.1f "
            "request_timeout_seconds=%s error_type=%s",
            path.name,
            elapsed_seconds,
            request_timeout_seconds,
            exc.__class__.__name__,
        )
        raise MinerUServiceUnavailable(
            f"MinerU 本地解析请求在 {request_timeout_seconds} 秒后超时；"
            "为避免并发占满服务器，系统不会自动回退到其他解析器。"
        ) from exc
    except httpx.HTTPError as exc:
        elapsed_seconds = time.monotonic() - started_at
        logger.warning(
            "Knowledge parse stage request failed: parser=mineru file=%s elapsed_seconds=%.1f "
            "error_type=%s",
            path.name,
            elapsed_seconds,
            exc.__class__.__name__,
        )
        raise MinerUServiceUnavailable(
            f"MinerU 本地解析服务不可达：{exc.__class__.__name__}；"
            "为避免并发占满服务器，系统不会自动回退到其他解析器。"
        ) from exc

    elapsed_seconds = time.monotonic() - started_at
    logger.info(
        "Knowledge parse stage completed: parser=mineru file=%s status_code=%s elapsed_seconds=%.1f",
        path.name,
        response.status_code,
        elapsed_seconds,
    )

    if response.status_code >= 400:
        try:
            detail = str(response.json().get("detail", "MinerU 服务返回失败"))
        except Exception:
            detail = response.text or "MinerU 服务返回失败"
        message = f"MinerU 解析失败（HTTP {response.status_code}）：{detail[:500]}"
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            raise MinerUServiceUnavailable(
                f"{message}；为避免并发占满服务器，系统不会自动回退到其他解析器。"
            )
        raise MinerUConversionFailed(message)

    try:
        markdown = _ensure_readable_markdown(response.json().get("markdown", ""), "MinerU")
    except ValueError as exc:
        raise MinerUConversionFailed(str(exc)) from exc
    except Exception as exc:
        raise MinerUConversionFailed(f"MinerU 返回内容无效：{str(exc)[:300]}") from exc

    return ParsedDocument(
        markdown=markdown,
        metadata={"source_name": path.name},
        parser="mineru",
        warnings=[],
    )


def _docling_convert(path: Path) -> ParsedDocument:
    started_at = time.monotonic()
    logger.info("Knowledge parse stage started: parser=docling file=%s", path.name)
    try:
        # A converter owns heavyweight model state.  It is intentionally cached
        # and protected by a lock because callers may include authorized
        # attachment parsing outside the knowledge-document queue.
        with _DOCLING_CONVERTER_LOCK:
            result = _docling_converter().convert(path)
        if result.has_timeout_errors():
            raise RuntimeError(
                "Docling 本地解析达到单文件时限，未将不完整内容写入知识库。"
            )
        document = result.document
        markdown = _ensure_readable_markdown(document.export_to_markdown(), "Docling")
    except Exception:
        logger.exception(
            "Knowledge parse stage failed: parser=docling file=%s elapsed_seconds=%.1f",
            path.name,
            time.monotonic() - started_at,
        )
        raise
    try:
        payload = document.model_dump(mode="json")
    except Exception:
        payload = {"source": path.name}
    logger.info(
        "Knowledge parse stage completed: parser=docling file=%s elapsed_seconds=%.1f characters=%s",
        path.name,
        time.monotonic() - started_at,
        len(markdown),
    )
    return ParsedDocument(
        markdown=markdown,
        metadata={"document": payload, "source_name": path.name},
        parser="docling",
        warnings=[],
    )


def _paddle_ocr_fallback(
    path: Path,
    mineru_error: Exception | None,
    docling_error: Exception,
) -> ParsedDocument:
    """Use PaddleOCR only when both MinerU and Docling cannot read a PDF."""
    started_at = time.monotonic()
    logger.info("Knowledge parse stage started: parser=paddleocr file=%s", path.name)
    try:
        # Keep one initialized OCR model in this API process.  Repeated
        # construction was repeatedly loading CPU models for every fallback.
        with _PADDLE_OCR_LOCK:
            result = _paddle_ocr().predict(str(path))
        text_parts: list[str] = []
        for page_index, item in enumerate(result or [], start=1):
            payload = item.json if hasattr(item, "json") else item
            if isinstance(payload, dict):
                payload = payload.get("res", payload)
                recognised = payload.get("rec_texts", []) if isinstance(payload, dict) else []
                recognised = [str(value).strip() for value in recognised if str(value).strip()]
                if recognised:
                    text_parts.append(f"### OCR page {page_index}\n" + "\n".join(recognised))
                    continue
            text_parts.append(json.dumps(payload, ensure_ascii=False) if isinstance(payload, (dict, list)) else str(payload))

        markdown = _ensure_readable_markdown("\n\n".join(text_parts), "PaddleOCR")
        stage_failures = [
            f"MinerU 解析失败：{_error_summary(mineru_error)}" if mineru_error else "",
            f"Docling 解析失败：{_error_summary(docling_error)}",
        ]
        warning = "；".join(item for item in stage_failures if item)
        warning = f"{warning}；已使用 PaddleOCR 本地备用解析。"
        logger.info(
            "Knowledge parse stage completed: parser=paddleocr file=%s elapsed_seconds=%.1f characters=%s",
            path.name,
            time.monotonic() - started_at,
            len(markdown),
        )
        return ParsedDocument(
            markdown=markdown,
            metadata={"source_name": path.name},
            parser="paddleocr",
            warnings=[warning],
        )
    except Exception as fallback_error:
        logger.exception(
            "Knowledge parse stage failed: parser=paddleocr file=%s elapsed_seconds=%.1f",
            path.name,
            time.monotonic() - started_at,
        )
        details = [
            f"MinerU：{_error_summary(mineru_error)}" if mineru_error else "",
            f"Docling：{_error_summary(docling_error)}",
            f"PaddleOCR：{_error_summary(fallback_error)}",
        ]
        raise RuntimeError(
            f"MinerU、Docling 与 PaddleOCR 均未能解析该文件：{'；'.join(item for item in details if item)}"
        ) from fallback_error


async def parse_local_document(path: Path) -> ParsedDocument:
    """Parse a local, authorized attachment without sending the file off-host."""
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError("暂不支持该附件格式。")

    queued_at = time.monotonic()
    await asyncio.to_thread(_LOCAL_PARSE_PIPELINE_LOCK.acquire)
    try:
        logger.info(
            "Knowledge parse pipeline slot acquired: file=%s queue_wait_seconds=%.1f",
            path.name,
            time.monotonic() - queued_at,
        )
        mineru_error: Exception | None = None
        if suffix in MINERU_SUPPORTED_SUFFIXES:
            try:
                return await asyncio.to_thread(_mineru_convert, path)
            except MinerUServiceUnavailable as exc:
                logger.warning(
                    "Knowledge parser stopped without fallback: file=%s parser=mineru error=%s",
                    path.name,
                    _error_summary(exc),
                )
                raise
            except Exception as exc:
                mineru_error = exc
                logger.warning(
                    "Knowledge parser fallback selected: file=%s failed_parser=mineru error=%s",
                    path.name,
                    _error_summary(exc),
                )

        try:
            parsed = await asyncio.to_thread(_docling_convert, path)
        except Exception as exc:
            if suffix == ".pdf":
                return await asyncio.to_thread(_paddle_ocr_fallback, path, mineru_error, exc)
            mineru_detail = f"MinerU 失败：{str(mineru_error)[:160]}；" if mineru_error else ""
            raise RuntimeError(f"{mineru_detail}Docling 未能解析该文件：{str(exc)[:160]}") from exc

        if mineru_error:
            parsed.warnings.append(f"MinerU 未能得到可用结果，已改用 Docling 本地解析：{str(mineru_error)[:160]}")
        return parsed
    finally:
        _LOCAL_PARSE_PIPELINE_LOCK.release()


def serialize_parsed_document(parsed: ParsedDocument) -> dict[str, Any]:
    return asdict(parsed)
