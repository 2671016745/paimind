#!/usr/bin/env python3
"""paimind API 的 Python 客户端示例。

    python examples/python_client.py                  # 本地决策 / local decide
    python examples/python_client.py --online         # 强制联网 / force online
    python examples/python_client.py --stream         # SSE 流式 / SSE streaming
    python examples/python_client.py --tier L2        # 指定档位 / pin a tier
    python examples/python_client.py --base http://192.168.1.10:8686

依赖 / Dependencies: requests（已在 requirements.txt 中）
"""

from __future__ import annotations

import argparse
import json
import sys

import requests

DEFAULT_BASE = "http://127.0.0.1:8686"


def decide(base: str, payload: dict) -> dict:
    r = requests.post(f"{base}/v1/decide", json=payload, timeout=120)
    r.raise_for_status()
    return r.json()


def decide_stream(base: str, payload: dict) -> dict:
    """Consume the SSE stream and return the final result."""

    result = {}
    with requests.post(f"{base}/v1/decide/stream", json=payload, stream=True, timeout=120) as r:
        r.raise_for_status()
        event = None
        for line in r.iter_lines():
            if not line:
                continue
            text = line.decode("utf-8")
            if text.startswith("event:"):
                event = text[6:].strip()
                print(f"  [{event}]", flush=True)
            elif text.startswith("data:"):
                data = json.loads(text[5:].strip() or "{}")
                if event == "token":
                    sys.stdout.write(data.get("delta", ""))
                    sys.stdout.flush()
                elif event == "result":
                    result = data
    print()
    return result


def show(result: dict) -> None:
    selected = result.get("selected", {})
    source_map = {"local": "本地", "online": "联网", "hybrid": "本地+联网"}
    print(f"选中 / selected : {selected.get('text')}")
    print(f"理由 / reason   : {result.get('reason')}")
    print(f"置信度 / conf   : {selected.get('confidence')}")
    print(f"来源 / source   : {source_map.get(result.get('source'), result.get('source'))}")
    print(f"后端 / backend  : {result.get('backend')}  档位 / tier: {result.get('tier')}")
    print(f"延迟 / latency  : {result.get('latency_ms')} ms")
    if result.get("sources"):
        print("来源列表 / sources:")
        for s in result["sources"]:
            print(f"  - {s.get('title')}  ({s.get('domain')})")
    if result.get("alternatives"):
        print("候选 / alternatives:")
        for a in result["alternatives"]:
            print(f"  - {a.get('text')}  {a.get('score') * 100:.0f}%")


def main() -> int:
    parser = argparse.ArgumentParser(description="paimind Python client example")
    parser.add_argument("--base", default=DEFAULT_BASE, help="API base URL")
    parser.add_argument("--online", action="store_true", help="强制联网 / force online")
    parser.add_argument("--stream", action="store_true", help="SSE 流式 / stream via SSE")
    parser.add_argument("--tier", default="auto", help="auto / L0..L3")
    parser.add_argument("--question", default="周末两天，预算 500，两个人出行，去哪里？")
    args = parser.parse_args()

    payload = {
        "question": args.question,
        "options": [
            {"id": "a", "text": "近郊露营"},
            {"id": "b", "text": "周边古镇一日游"},
        ],
        "mode": "online" if args.online else "auto",
        "model_tier": args.tier,
    }

    print(f"POST {args.base}/v1/decide  payload={json.dumps(payload, ensure_ascii=False)}\n")
    try:
        result = decide_stream(args.base, payload) if args.stream else decide(args.base, payload)
    except requests.HTTPError as exc:
        body = exc.response.json() if exc.response.content else {}
        error = body.get("error", {})
        print(f"HTTP {exc.response.status_code} {error.get('code')}: {error.get('message')}")
        if error.get("details"):
            print(json.dumps(error["details"], ensure_ascii=False, indent=2))
        return 1

    show(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
