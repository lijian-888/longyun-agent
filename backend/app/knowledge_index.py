"""Local BGE-M3 embedding and deterministic document chunking for the knowledge base."""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path

import torch
from transformers import AutoModel, AutoTokenizer


EMBEDDING_DIMENSION = 1024
MIN_PARSED_CHARACTERS = 80
_model: tuple[object, object] | None = None
_model_lock = threading.Lock()


class KnowledgeIndexUnavailable(RuntimeError):
    """The document is parsed, but its approved local embedding runtime is unavailable."""


def local_model_path() -> Path:
    return Path(os.getenv("BGE_M3_MODEL_PATH", "/data/models/bge-m3"))


def ensure_bge_m3_model() -> object:
    """Load only an on-host BGE-M3 model; never download a model during a user request."""
    global _model
    with _model_lock:
        if _model is not None:
            return _model
        model_path = local_model_path()
        if not model_path.is_dir():
            raise KnowledgeIndexUnavailable(
                "本地 bge-m3 模型尚未就绪。请由管理员先执行模型预热，再重试知识库索引。"
            )
        try:
            tokenizer = AutoTokenizer.from_pretrained(str(model_path), local_files_only=True)
            model = AutoModel.from_pretrained(str(model_path), local_files_only=True)
            model.eval()
            _model = (tokenizer, model)
        except Exception as exc:  # pragma: no cover - depends on local model artefacts
            raise KnowledgeIndexUnavailable("本地 bge-m3 模型无法加载，请检查模型文件和服务日志。") from exc
        return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Return dense BGE-M3 vectors for short, already-authorized local chunks."""
    if not texts:
        return []
    tokenizer, model = ensure_bge_m3_model()
    try:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 8):
            batch = texts[start: start + 8]
            inputs = tokenizer(batch, padding=True, truncation=True, max_length=8192, return_tensors="pt")
            with torch.no_grad():
                output = model(**inputs).last_hidden_state[:, 0]
                output = torch.nn.functional.normalize(output, p=2, dim=1)
            vectors.extend(output.cpu().tolist())
        result = [[float(value) for value in vector] for vector in vectors]
        if any(len(vector) != EMBEDDING_DIMENSION for vector in result):
            raise ValueError("Unexpected BGE-M3 vector dimension")
        return result
    except KnowledgeIndexUnavailable:
        raise
    except Exception as exc:  # pragma: no cover - model runtime varies by host
        raise KnowledgeIndexUnavailable("本地 bge-m3 向量化失败，请检查模型服务日志。") from exc


def normalize_markdown(markdown: str) -> str:
    text = re.sub(r"\r\n?", "\n", markdown or "")
    # Defensive normalization for older parser output that reached the index
    # before document_parser began converting escaped range markers.
    text = text.replace("\\~", "～")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_markdown(markdown: str, *, max_characters: int = 1100, overlap: int = 160) -> list[tuple[str, str]]:
    """Split on headings and paragraphs while retaining a stable source locator."""
    text = normalize_markdown(markdown)
    if len(text) < MIN_PARSED_CHARACTERS:
        raise ValueError(f"解析文本少于 {MIN_PARSED_CHARACTERS} 个字符，未达到知识库最小可用性要求。")

    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    chunks: list[tuple[str, str]] = []
    pending = ""
    start_block = 1

    def append_chunk(value: str, first_block: int, last_block: int) -> None:
        cleaned = value.strip()
        if cleaned:
            chunks.append((cleaned, f"第 {first_block}-{last_block} 段"))

    for index, block in enumerate(blocks, start=1):
        candidate = f"{pending}\n\n{block}".strip() if pending else block
        if pending and len(candidate) > max_characters:
            append_chunk(pending, start_block, index - 1)
            tail = pending[-overlap:] if overlap else ""
            pending = f"{tail}\n\n{block}".strip()
            start_block = max(1, index - 1)
        else:
            pending = candidate
    append_chunk(pending, start_block, len(blocks))

    expanded: list[tuple[str, str]] = []
    for content, locator in chunks:
        if len(content) <= max_characters * 1.35:
            expanded.append((content, locator))
            continue
        for offset in range(0, len(content), max_characters - overlap):
            part = content[offset: offset + max_characters].strip()
            if part:
                expanded.append((part, f"{locator}，字符 {offset + 1}-{offset + len(part)}"))
            if offset + max_characters >= len(content):
                break
    return expanded
