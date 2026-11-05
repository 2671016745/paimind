#!/usr/bin/env bash
# Ollama / vLLM / llama.cpp 安装与启动示例
# Install and start Ollama / vLLM / llama.cpp.
#
#   bash examples/engine_setup.sh ollama
#   bash examples/engine_setup.sh vllm
#   bash examples/engine_setup.sh llamacpp
set -euo pipefail

ENGINE="${1:-ollama}"
shift || true
EXTRA_ARGS=("$@")

hr() { printf '\n\033[1m=== %s ===\033[0m\n' "$1"; }

case "$ENGINE" in
  ollama)
    hr "Ollama 安装 / install"
    if ! command -v ollama >/dev/null 2>&1; then
      case "$(uname -s)" in
        Linux) curl -fsSL https://ollama.com/install.sh | sh ;;
        Darwin)
          if command -v brew >/dev/null 2>&1; then
            brew install ollama
          else
            echo "请从 https://ollama.com/download 下载 macOS 安装包"
            echo "Download the macOS package from https://ollama.com/download"
            exit 1
          fi
          ;;
        MINGW*|MSYS*|CYGWIN*)
          echo "Windows 请从 https://ollama.com/download 下载安装包后重新运行"
          echo "On Windows, install from https://ollama.com/download, then re-run"
          exit 1
          ;;
        *) echo "不支持的平台 / unsupported platform: $(uname -s)"; exit 1 ;;
      esac
    fi

    hr "Ollama 启动 / start"
    ollama serve >/dev/null 2>&1 &
    sleep 2

    hr "拉取模型 / pull models"
    ollama pull qwen2.5:0.5b
    ollama pull qwen2.5:1.5b
    ollama pull qwen2.5:3b

    hr "验证 / verify"
    ollama list
    curl -s http://localhost:11434/v1/models | head -c 200; echo
    echo "App 侧无需改动：engines.ollama.base_url 默认就是 http://localhost:11434"
    echo "No app change needed: engines.ollama.base_url already defaults to http://localhost:11434"
    ;;

  vllm)
    hr "vLLM（需 NVIDIA GPU + Linux）/ vLLM (needs NVIDIA GPU + Linux)"
    if [[ "$(uname -s)" != "Linux" ]]; then
      echo "❌ vLLM 只支持 Linux + NVIDIA GPU。Windows 请在 WSL2 里运行本脚本。"
      echo "   vLLM requires Linux + NVIDIA GPU. On Windows, run this inside WSL2."
      exit 1
    fi
    if ! command -v nvidia-smi >/dev/null 2>&1; then
      echo "❌ 未检测到 NVIDIA GPU / no NVIDIA GPU detected"
      exit 1
    fi
    nvidia-smi --query-gpu=name,memory.total --format=csv

    hr "安装 / install"
    python3 -m venv .venv-vllm
    # shellcheck disable=SC1091
    source .venv-vllm/bin/activate
    pip install -r requirements-vllm.txt

    hr "启动 / serve"
    MODEL="${EXTRA_ARGS[0]:-Qwen/Qwen2.5-7B-Instruct-AWQ}"
    vllm serve "$MODEL" --quantization awq --max-model-len 4096 --port 8000
    ;;

  llamacpp)
    hr "llama.cpp / llama-server"
    case "$(uname -s)" in
      Linux)   ARCH="linux-x64" ;;
      Darwin)  ARCH="macos-arm64" ;;
      MINGW*|MSYS*|CYGWIN*) ARCH="win-x64" ;;
      *) echo "不支持的平台 / unsupported: $(uname -s)"; exit 1 ;;
    esac
    echo "架构 / arch: $ARCH"
    echo "从 https://github.com/ggml-org/llama.cpp/releases 下载预编译包并解压到 .tools/llama.cpp"
    echo "Download a prebuilt release into .tools/llama.cpp"

    hr "启动 / start"
    GGUF="${EXTRA_ARGS[0]:-qwen2.5-1.5b-q4_k_m.gguf}"
    if [[ -x ".tools/llama.cpp/llama-server" ]]; then
      # -fa on 必须给，否则 CUDA offload 静默失败退回 CPU（实测 53.9 s → 3.5 s）
      # *-fa on is required or CUDA offload silently falls back to CPU*
      .tools/llama.cpp/llama-server -m "$GGUF" --port 8080 -ngl 99 -fa on -c 4096
    else
      echo "未找到 .tools/llama.cpp/llama-server，请先下载 / download it first"
    fi
    ;;

  *)
    echo "用法 / usage: bash examples/engine_setup.sh {ollama|vllm|llamacpp} [extra args]"
    exit 1
    ;;
esac
