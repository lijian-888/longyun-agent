"""Download and validate local document/OCR models before an offline rollout.

Run once while the deployment host is allowed to access approved model
registries. The cache is written to the mounted ``model_data`` Docker volume.
"""

import os
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas

from .document_parser import _docling_converter, _paddle_ocr
from .knowledge_index import EMBEDDING_DIMENSION, embed_texts, local_model_path


def download_bge_m3_model(model_path) -> None:
    """Download BGE-M3 only during controlled administrator warmup.

    The application never downloads a model in a researcher request.  The
    source is configurable because many intranet servers can reach ModelScope
    but cannot reach Hugging Face directly.
    """
    source = os.getenv("BGE_M3_MODEL_SOURCE", "huggingface").strip().lower()
    repository = os.getenv("BGE_M3_MODEL_REPOSITORY", "BAAI/bge-m3").strip()
    if not repository:
        raise RuntimeError("BGE_M3_MODEL_REPOSITORY 不能为空。")

    if source == "modelscope":
        from modelscope.hub.snapshot_download import snapshot_download

        snapshot_download(repository, local_dir=str(model_path))
        return
    if source == "huggingface":
        from huggingface_hub import snapshot_download

        endpoint = os.getenv("BGE_M3_HF_ENDPOINT", "").strip() or None
        snapshot_download(repository, local_dir=str(model_path), endpoint=endpoint)
        return
    if source == "local":
        return
    raise RuntimeError("BGE_M3_MODEL_SOURCE 仅支持 modelscope、huggingface 或 local。")


def _write_docling_probe(destination: Path) -> None:
    document = canvas.Canvas(str(destination))
    document.setFont("Helvetica", 14)
    document.drawString(72, 720, "Longyun Agent local Docling warmup")
    document.save()


def _write_paddle_probe(destination: Path) -> None:
    image = Image.new("RGB", (720, 180), color="white")
    painter = ImageDraw.Draw(image)
    painter.text((24, 70), "Longyun Agent OCR warmup 123", fill="black")
    image.save(destination)


def _warm_docling_and_paddle_models() -> None:
    """Exercise inference once so model artefacts, not only Python objects, exist."""
    with tempfile.TemporaryDirectory(prefix="longyun-model-warmup-") as temporary_directory:
        temporary_path = Path(temporary_directory)

        docling_probe = temporary_path / "docling-warmup.pdf"
        _write_docling_probe(docling_probe)
        docling_markdown = _docling_converter().convert(docling_probe).document.export_to_markdown().strip()
        if not docling_markdown:
            raise RuntimeError("Docling warmup did not produce readable Markdown.")

        paddle_probe = temporary_path / "paddleocr-warmup.png"
        _write_paddle_probe(paddle_probe)
        paddle_result = list(_paddle_ocr().predict(str(paddle_probe)))
        if not paddle_result:
            raise RuntimeError("PaddleOCR warmup did not produce an inference result.")


def main() -> None:
    _warm_docling_and_paddle_models()
    model_path = local_model_path()
    if not model_path.is_dir():
        download_bge_m3_model(model_path)
    has_weights = any((model_path / name).is_file() for name in ("pytorch_model.bin", "model.safetensors"))
    if not (model_path / "config.json").is_file() or not has_weights:
        raise RuntimeError("BGE-M3 model files are incomplete after warmup.")
    vectors = embed_texts(["Longyun Agent embedding warmup"])
    if len(vectors) != 1 or len(vectors[0]) != EMBEDDING_DIMENSION:
        raise RuntimeError("BGE-M3 warmup did not produce the expected embedding vector.")
    # A separate warmup container cannot share in-memory model weights with the
    # API process, but the verified local artefacts are persisted in model_data.
    print("Docling, PaddleOCR, and BGE-M3 local model files are ready for offline use.")


if __name__ == "__main__":
    main()
