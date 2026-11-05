# paimind App API 镜像 / paimind App API image
#
# 只打包 API 服务（不含桌面 UI），用于部署到任意支持 Python 的 PaaS / VM。
# 若要把 vLLM 作为独立推理服务，本镜像可作为轻量网关跑在 CPU 节点，
# 通过 engines.vllm.base_url 指向 GPU 节点。见 docs/deploy.md。
#
# Packages only the API (no desktop UI) for any Python-capable PaaS/VM.
# With vLLM as a separate inference service this image can run as a thin
# gateway on a CPU node, pointing engines.vllm.base_url at the GPU node.
# See docs/deploy.md.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PAIMIND_ROOT=/app

# pip 源：默认走官方 PyPI；构建时可通过 --build-arg PIP_INDEX_URL=... 换成镜像。
# 本机（中国区网络）直连 PyPI 会间歇性丢包，导致构建在不同包上随机失败；
# 换镜像或加重试能显著改善。/ PyPI is flaky on some networks — override or retry.
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_EXTRA_INDEX_URL=
ARG PIP_RETRIES=5
ARG PIP_TIMEOUT=60

WORKDIR /app

# 系统依赖：libgomp 是 onnxruntime CPU 版需要的。
# libgomp is required by the CPU build of onnxruntime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

# 只装 API 依赖：不含 PySide6（那是桌面 UI，镜像里用不到，省 ~400 MB 下载）。
# API-only deps: no PySide6 (desktop UI) — the image never imports it.
COPY requirements-api.txt ./requirements.txt
RUN pip install --no-cache-dir \
        --retries "${PIP_RETRIES}" \
        --timeout "${PIP_TIMEOUT}" \
        --index-url "${PIP_INDEX_URL}" \
        -r requirements.txt

COPY core ./core
COPY server ./server
COPY models ./models
COPY config.example.yaml ./config.yaml

# 对外只暴露 API；模型通过卷挂载或下载填充。
# Only the API port is exposed; models come from a volume or a download.
EXPOSE 8686

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8686/health || exit 1

CMD ["python", "-m", "uvicorn", "server.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8686"]
