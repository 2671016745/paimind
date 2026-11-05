#!/usr/bin/env python3
"""用 Ollama 模式跑一次 paimind 决策。

    python examples/ollama_client.py
    python examples/ollama_client.py --base http://192.168.1.10:11434 --model qwen2.5:1.5b

前置 / Prerequisites:
    1. Ollama 已安装并启动；
    2. 已拉取模型：ollama pull qwen2.5:1.5b
    3. paimind 的 engines.ollama.base_url 指向该地址（默认 localhost:11434）

本示例直接调用 Ollama 的 OpenAI 兼容接口，演示「外部引擎统一走 /v1/chat/completions」
这条设计 —— App 内部对 Ollama / vLLM / llama.cpp 用的是同一个客户端。
This calls Ollama's OpenAI-compatible endpoint directly, demonstrating the
"all engines share one /v1/chat/completions client" design.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from core.inference import OllamaBackend  # noqa: E402
from core.inference.base import ChatMessage  # noqa: E402
from core.prompt import build_decision_prompt, parse_decision  # noqa: E402
from core.schemas import Option  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="paimind over Ollama")
    parser.add_argument("--base", default="http://localhost:11434", help="Ollama base URL")
    parser.add_argument("--model", default="qwen2.5:1.5b", help="Ollama model tag")
    parser.add_argument("--question", default="周末两天，预算 500，两个人出行，去哪里？")
    args = parser.parse_args()

    options = [Option(id="a", text="近郊露营"), Option(id="b", text="周边古镇一日游")]

    config = {
        "base_url": args.base,
        "openai_base_url": f"{args.base.rstrip('/')}/v1",
        "timeout_seconds": 60,
    }
    backend = OllamaBackend(config, args.model)

    print(f"Ollama / {args.model} @ {args.base}")
    print(f"ready: {backend.ready()}   kind: {backend.kind().value}")
    if not backend.ready():
        print("Ollama 不可达，请先 / unreachable — run: ollama serve && ollama pull qwen2.5:1.5b")
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
    print(f"tokens          : {completion.prompt_tokens} + {completion.completion_tokens}")
    print(f"延迟  / latency : {completion.latency_ms} ms")
    backend.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
