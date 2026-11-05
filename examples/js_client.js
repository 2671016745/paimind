#!/usr/bin/env node
/**
 * paimind API 的 JavaScript 客户端示例。
 *
 *   node examples/js_client.js                  # 本地决策
 *   node examples/js_client.js --online         # 强制联网
 *   node examples/js_client.js --stream         # SSE 流式
 *   node examples/js_client.js --tier L2 --base http://192.168.1.10:8686
 *
 * 无需第三方依赖（Node 18+ 自带 fetch）。
 * No third-party dependencies (Node 18+ ships fetch).
 */

const DEFAULT_BASE = 'http://127.0.0.1:8686';

function parseArgs(argv) {
  const args = { base: DEFAULT_BASE, online: false, stream: false, tier: 'auto', question: '周末两天，预算 500，两个人出行，去哪里？' };
  for (let i = 2; i < argv.length; i += 1) {
    const flag = argv[i];
    if (flag === '--online') args.online = true;
    else if (flag === '--stream') args.stream = true;
    else if (flag === '--base') { args.base = argv[i + 1]; i += 1; }
    else if (flag === '--tier') { args.tier = argv[i + 1]; i += 1; }
    else if (flag === '--question') { args.question = argv[i + 1]; i += 1; }
  }
  return args;
}

const SOURCE_LABEL = { local: '本地', online: '联网', hybrid: '本地+联网' };

function show(result) {
  const selected = result.selected || {};
  console.log(`选中 / selected : ${selected.text}`);
  console.log(`理由 / reason   : ${result.reason}`);
  console.log(`置信度 / conf   : ${selected.confidence}`);
  console.log(`来源 / source   : ${SOURCE_LABEL[result.source] || result.source}`);
  console.log(`后端 / backend  : ${result.backend}  档位 / tier: ${result.tier}`);
  console.log(`延迟 / latency  : ${result.latency_ms} ms`);
  for (const s of result.sources || []) console.log(`  来源 / source  : ${s.title} (${s.domain})`);
  for (const a of result.alternatives || []) console.log(`  候选 / alt     : ${a.text}  ${Math.round(a.score * 100)}%`);
}

async function decide(base, payload) {
  const r = await fetch(`${base}/v1/decide`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const body = await r.json();
  if (!r.ok) throw Object.assign(new Error(body.error?.message || r.statusText), { body, status: r.status });
  return body;
}

async function decideStream(base, payload) {
  const r = await fetch(`${base}/v1/decide/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    const body = await r.json();
    throw Object.assign(new Error(body.error?.message || r.statusText), { body, status: r.status });
  }

  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let event = null;
  let result = null;

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop();
    for (const line of lines) {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:')) {
        const raw = line.slice(5).trim();
        let data = {};
        try { data = raw ? JSON.parse(raw) : {}; } catch { /* ignore */ }
        if (event === 'token') process.stdout.write(data.delta || '');
        else if (event === 'result') { result = data; console.log(); }
        else if (event === 'meta') console.log(`  [meta] ${data.model} / ${data.backend}`);
      }
    }
  }
  return result;
}

async function main() {
  const args = parseArgs(process.argv);
  const payload = {
    question: args.question,
    options: [
      { id: 'a', text: '近郊露营' },
      { id: 'b', text: '周边古镇一日游' },
    ],
    mode: args.online ? 'online' : 'auto',
    model_tier: args.tier,
  };

  console.log(`POST ${args.base}/v1/decide  ${JSON.stringify(payload)}\n`);
  try {
    const result = args.stream ? await decideStream(args.base, payload) : await decide(args.base, payload);
    show(result);
  } catch (err) {
    const e = err.body?.error || {};
    console.error(`HTTP ${err.status} ${e.code}: ${e.message}`);
    if (e.details) console.error(JSON.stringify(e.details, null, 2));
    process.exitCode = 1;
  }
}

main();
