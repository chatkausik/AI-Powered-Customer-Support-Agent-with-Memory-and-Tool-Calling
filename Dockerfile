FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip uv

COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-dev --no-cache

COPY . /app

# Pre-download the ChromaDB default ONNX embedding model so it is baked into
# the image and never downloaded at runtime (avoids corrupted-protobuf errors).
RUN .venv/bin/python -c "from chromadb.utils.embedding_functions import DefaultEmbeddingFunction; DefaultEmbeddingFunction()"

EXPOSE 8000 8501

CMD ["uv", "run", "python", "main.py"]
