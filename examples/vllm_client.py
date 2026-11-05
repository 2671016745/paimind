#!/usr/bin/env python3
"""用 vLLM 模式跑一次 paimind 决策。

    python examples/vllm_client.py
    python examples/vllm_client.py --base http://localhost:8000 --model Qwen/Qwen2.5-3B-Instruct-AWQ

> **需要 NVIDIA GPU + Linux。** Windows / macOS 不可用（Windows 请在 WSL2 里跑）。
> ***Requires NVIDIA GPU + Linux.*** *Not available on Windows or macOS (use WSL2 on Windows).*

前置 / Prerequisites:
    1. vLLM 已安装并启动：
       vllm serve Qwen/Qwen2.5-3B-Instruct-AWQ --quantization awq --max-model-len 4096 --port 8000
    2. paimind 的 engines.vllm.base_url 指向该地址（默认 localhost:8000）

与 Ollama 示例走的是同一个客户端 —— App 层不感知引擎差异。
Uses the same client as the Ollama example — the app layer never sees engine differences.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.inference import VllmBackend  # noqa: E402
from core.inference.base import ChatMessage  # noqa: E402
from core.prompt import build_decision_prompt, parse_decision  # noqa: E402
from core.schemas import Option  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="paimind over vLLM")
    parser.add_argument("--base", default="http://localhost:8000", help="vLLM base URL")
    parser.add_argument("--model", default="Qwen/Qwen2.5-3B-Instruct-AWQ", help="served model id")
    parser.add_argument("--question", default="周末两天，预算 500，两个人出行，去哪里？")
    args = parser.parse_args()

    options = [Option(id="a", text="近郊露营"), Option(id="b", text="周边古镇一日游")]

    config = {
        "base_url": args.base,
        "openai_base_url": f"{args.base.rstrip('/')}/v1",
        "timeout_seconds": 60,
    }
    backend = VllmBackend(config, args.model)

    print(f"vLLM / {args.model} @ {args.base}")
    print(f"ready: {backend.ready()}   kind: {backend.kind().value}")
    if not backend.ready():
        print("vLLM 不可达 / unreachable — run: vllm serve <model> --port 8000")
        return 1

    system, user = build_decision_prompt(args.question, options)
    completion = backend.complete(
        [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)],
        max_tokens=256,
        temperature=0.3,
    )
    parsed = parse_decision(completion.text, options)

    print(f"\nraw   / 原始输出: {completion.text.strip()[:200]}")
    print(f"选中  / selected : {[o.text for o in options if o.id == parsed.selected_id]}")
    print(f"理由  / reason   : {parsed.reason}")
    print(f"置信度/ conf    : {parsed.confidence}")
    print(f"延迟  / latency : {completion.latency_ms} ms")
    backend.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
