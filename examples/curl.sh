#!/usr/bin/env bash
# paimind API 调用示例 / paimind API examples
# 用法 / Usage:  bash examples/curl.sh
set -euo pipefail

BASE="${PAIMIND_BASE:-http://127.0.0.1:8686}"

hr() { printf '\n\033[1m=== %s ===\033[0m\n' "$1"; }

hr "health 存活检查"
curl -s "$BASE/health" | python -m json.tool

hr "hardware 硬件检测"
curl -s "$BASE/v1/hardware" | python -m json.tool

hr "models 档位三态"
curl -s "$BASE/v1/models" | python -m json.tool

hr "engines 引擎状态"
curl -s "$BASE/v1/engines" | python -m json.tool

hr "decide 本地决策"
curl -s -X POST "$BASE/v1/decide" \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "周末两天，预算 500，两个人出行，去哪里？",
    "options": [
      {"id": "a", "text": "近郊露营"},
      {"id": "b", "text": "周边古镇一日游"}
    ],
    "mode": "auto",
    "model_tier": "auto"
  }' | python -m json.tool

hr "decide 强制联网"
curl -s -X POST "$BASE/v1/decide" \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "今天上海天气怎么样",
    "options": [{"id": "a", "text": "适合出游"}, {"id": "b", "text": "不适合"}],
    "mode": "online"
  }' | python -m json.tool

hr "decide/stream 流式（SSE）"
curl -N -s -X POST "$BASE/v1/decide/stream" \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "选哪个",
    "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}]
  }'

hr "download-sources 下载源"
curl -s "$BASE/v1/download-sources" | python -m json.tool

hr "test mirror 测试单个镜像"
curl -s -X POST "$BASE/v1/download-sources/github/official/test?tier=L1&filename=model.onnx" \
  | python -m json.tool

hr "usage 用量"
curl -s "$BASE/v1/usage" | python -m json.tool
