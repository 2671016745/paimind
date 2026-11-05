#!/usr/bin/env bash
# 在 WSL2 (Ubuntu) 里启动 vLLM —— **需要 NVIDIA GPU + Linux**
# Start vLLM inside WSL2 (Ubuntu) — **requires NVIDIA GPU + Linux**.
#
#   Windows 用户：在 WSL 终端里运行本脚本。
#   Windows users: run this inside a WSL terminal.
#
# 用法 / Usage:
#   bash deploy/vllm-wsl.sh install     # 装 vllm（首次，约 3-5 GB）
#   bash deploy/vllm-wsl.sh serve       # 启动 3B AWQ
#   bash deploy/vllm-wsl.sh serve7b     # 启动 7B AWQ（需 ≥ 8 GB 显存）
#   bash deploy/vllm-wsl.sh status      # 看状态
#
# App 侧：engines.vllm.base_url 默认 http://localhost:8000，无需改动。
# App side: engines.vllm.base_url already defaults to http://localhost:8000.

set -euo pipefail

VENV="${PAIMIND_VLLM_VENV:-/mnt/d/ai/paimind/.vllm-venv}"
PORT="${PAIMIND_VLLM_PORT:-8000}"
# WSL2 的 localhost 会被 Windows 转发，所以 App 用 localhost:8000 就能连上。
# WSL2 forwards localhost, so the app reaches this via localhost:8000.

hr() { printf '\n\033[1m=== %s ===\033[0m\n' "$1"; }

require_gpu() {
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "❌ 未检测到 NVIDIA GPU / no NVIDIA GPU detected"
    echo "   vLLM 需要 NVIDIA GPU + Linux；请改用 builtin / Ollama / llama.cpp"
    exit 1
  fi
  nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
}

cmd_install() {
  hr "安装 vLLM / install"
  require_gpu
  sudo apt-get update -qq
  sudo apt-get install -y -qq python3-pip python3-venv
  python3 -m venv "$VENV"
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  pip install --upgrade pip -q
  pip install vllm
  python -c 'import vllm; print("vllm", vllm.__version__)'
}

cmd_serve() {
  hr "vLLM 3B AWQ"
  require_gpu
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  exec vllm serve Qwen/Qwen2.5-3B-Instruct-AWQ \
    --quantization awq \
    --max-model-len 4096 \
    --host 0.0.0.0 --port "$PORT" \
    --dtype auto \
    --gpu-memory-utilization 0.90
}

cmd_serve7b() {
  hr "vLLM 7B AWQ（需 ≥ 8 GB 显存）"
  require_gpu
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  exec vllm serve Qwen/Qwen2.5-7B-Instruct-AWQ \
    --quantization awq \
    --max-model-len 4096 \
    --host 0.0.0.0 --port "$PORT" \
    --dtype auto \
    --gpu-memory-utilization 0.90
}

cmd_status() {
  hr "状态 / status"
  if curl -fsS --max-time 5 "http://localhost:${PORT}/health" >/dev/null 2>&1; then
    echo "  ✅ vLLM 运行中 / running"
    curl -s "http://localhost:${PORT}/v1/models" | head -c 300; echo
  else
    echo "  ❌ 未运行 / not running"
  fi
  hr "GPU"
  command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader || echo "  无 GPU"
}

case "${1:-serve}" in
  install)  cmd_install ;;
  serve)    cmd_serve ;;
  serve7b)  cmd_serve7b ;;
  status)   cmd_status ;;
  *) echo "用法 / usage: bash deploy/vllm-wsl.sh {install|serve|serve7b|status}"; exit 1 ;;
esac
