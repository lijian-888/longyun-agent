FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=300

RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk curl unzip && rm -rf /var/lib/apt/lists/*
# PLINK2 is executed only by the internal genotype worker. Pin its Linux binary
# so converted VCFs and QC reports remain reproducible across deployments.
RUN curl -fsSL https://s3.amazonaws.com/plink2-assets/alpha7/plink2_linux_x86_64_20260504.zip -o /tmp/plink2.zip \
  && unzip -q /tmp/plink2.zip -d /tmp/plink2 \
  && install -m 0755 /tmp/plink2/plink2 /usr/local/bin/plink2 \
  && rm -rf /tmp/plink2 /tmp/plink2.zip
COPY requirements.txt .
# Docling depends on PyTorch. Pin pip to the CPU wheel index so a local,
# CPU-only deployment does not accidentally download several GB of CUDA libs.
RUN pip install --no-cache-dir --retries 8 --timeout 300 \
    --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt
# Keep the local GWAS numerical stack in a separate layer so changing it does
# not invalidate the much larger OCR/document-analysis dependency cache.
RUN pip install --no-cache-dir numpy==2.1.3 matplotlib==3.9.2
# Keep pgvector separate so adding the database adapter does not invalidate the
# heavy, already-cached Docling/PaddleOCR dependency layer.
RUN pip install --no-cache-dir pgvector==0.3.6
# OpenCV, pulled by PaddleOCR/PaddleX, needs these runtime libraries in the
# otherwise minimal Python image. Keep this before source copy so normal
# application-code changes do not re-download operating-system dependencies.
RUN apt-get -o Acquire::Retries=3 update && apt-get -o Acquire::Retries=3 install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 && rm -rf /var/lib/apt/lists/*
COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
