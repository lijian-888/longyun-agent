FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=300

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# The internal-network route to PyPI can be slow. Retry transient download
# failures instead of failing the whole production image build after 15s.
RUN pip install --no-cache-dir --retries 8 --timeout 300 \
    --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

COPY app.py warm_models.py .

EXPOSE 8001

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
