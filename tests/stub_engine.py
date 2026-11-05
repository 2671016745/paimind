"""A tiny OpenAI-compatible stub engine for tests and local verification.

Serves ``/v1/models`` and ``/v1/chat/completions`` so the app's external-engine
path (Ollama / vLLM / llama-server all speak this protocol) can be exercised
without a real model.

    python tests/stub_engine.py --port 11434 --model qwen2.5:1.5b

It answers with a deterministic, valid decision JSON so tests can assert on the
parsed result.
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OPTION_RE = re.compile(r"\[([A-Za-z0-9_\-]+)\]")


class Handler(BaseHTTPRequestHandler):
    model = "stub-model"
    latency = 0.0

    def log_message(self, *args):  # keep test output clean
        pass

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models") or self.path == "/api/tags":
            if self.path == "/api/tags":
                self._send(200, {"models": [{"name": self.model, "model": self.model}]})
            else:
                self._send(200, {"data": [{"id": self.model, "object": "model"}]})
            return
        if self.path.endswith("/health"):
            self._send(200, {"status": "ok"})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if "chat/completions" not in self.path:
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            payload = {}

        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = ["```json\n", '{"selected": "a", ', '"reason": "桩引擎确定性输出", ',
                      '"confidence": 0.91, ', '"alternatives": [{"id":"a","score":0.91}]}',
                      "\n```"]
            for chunk in chunks:
                data = {"choices": [{"delta": {"content": chunk}}]}
                self.wfile.write(f"data: {json.dumps(data)}\n\n".encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            return

        messages = payload.get("messages") or []
        user = ""
        for m in messages:
            if m.get("role") == "user":
                user = m.get("content", "")
        match = OPTION_RE.search(user)
        selected = match.group(1) if match else "a"
        text = json.dumps(
            {
                "selected": selected,
                "reason": "桩引擎确定性输出",
                "confidence": 0.91,
                "alternatives": [
                    {"id": selected, "score": 0.91},
                    {"id": "b", "score": 0.09},
                ],
            },
            ensure_ascii=False,
        )
        self._send(200, {
            "id": "stub-1",
            "object": "chat.completion",
            "model": payload.get("model", self.model),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
        })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=11434)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--model", default="qwen2.5:1.5b")
    args = parser.parse_args()

    Handler.model = args.model
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"stub engine listening on http://{args.host}:{args.port} model={args.model}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
