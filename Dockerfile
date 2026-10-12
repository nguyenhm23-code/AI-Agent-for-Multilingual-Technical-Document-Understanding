# syntax=docker/dockerfile:1
FROM nvidia/cuda:12.8.1-runtime-ubuntu24.04
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 HF_HUB_DISABLE_XET=1 \
    HF_HOME=/app/.cache/huggingface \
    DENSO_LLAMA_SERVER=/opt/llama/llama-server \
    PATH=/opt/venv/bin:${PATH} \
    LD_LIBRARY_PATH=/opt/llama:${LD_LIBRARY_PATH}
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 python3.12-venv ca-certificates libcurl4t64 libgomp1 libgl1 libglib2.0-0t64 \
    && rm -rf /var/lib/apt/lists/* \
    && python3.12 -m venv /opt/venv
COPY docker/install_runtime.py /tmp/install_runtime.py
RUN python /tmp/install_runtime.py && /opt/llama/llama-server --version
WORKDIR /app
COPY requirements.txt requirements.lock.txt cuda-constraints.txt ./
# The source lock was exported on Windows. Drop only its OS-specific entries;
# resolve CUDA wheels for Linux from the official PyTorch index instead.
RUN python -m pip install torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cu128
RUN sed -E '/^pywin32==/d; /^torch @ /d; /^torchvision @ /d' requirements.lock.txt > /tmp/linux-constraints.txt \
    && python -m pip install -r requirements.txt -c cuda-constraints.txt -c /tmp/linux-constraints.txt \
    && python -m pip check
COPY denso_a3/ ./denso_a3/
COPY configs/ ./configs/
COPY ground_truth/ ./ground_truth/
RUN mkdir -p .cache .local results evaluation/data \
    && python -m denso_a3 --help \
    && /opt/llama/llama-server --version
EXPOSE 8501
ENTRYPOINT ["python", "-m", "denso_a3.pipeline.container_cli"]
CMD ["rag-ui", "--host", "0.0.0.0"]
