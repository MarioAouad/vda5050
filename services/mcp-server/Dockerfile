# Build context is this service's own folder (services/mcp-server).
FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip setuptools wheel

COPY requirements.txt .
# CPU-only torch keeps the image well under the GPU build's size — this
# service does retrieval + reranking, not training, so CPU is sufficient.
# Pinned to match the exact version in requirements.txt so this step and the
# next one don't disagree and trigger a second, different torch download.
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu torch==2.13.0
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8001

CMD ["python", "-m", "server"]
