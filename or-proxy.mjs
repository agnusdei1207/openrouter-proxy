import http from 'node:http';

// OR_PROVIDER 예: '{"only":["baidu"],"allow_fallbacks":false}'
// - "baidu"는 base slug로 baidu/* 전부를 매칭 (docs: Base Slug Matching)
// - 정확 태그는 "baidu/fp8" (endpoints API 확인). 특정 endpoint로 고정하려면 ["baidu/fp8"] 사용.
const PROVIDER = JSON.parse(process.env.OR_PROVIDER || '{"only":["baidu"],"allow_fallbacks":false}');
const PORT = Number(process.env.PORT || 8787);

http.createServer(async (req, res) => {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  let body = Buffer.concat(chunks);
  const url = req.url || '/';
  // Claude Code는 /v1/messages(Anthropic), 직접 코드 예제는 /v1/chat/completions(OpenAI) 사용.
  // 둘 다 provider 주입 지원하도록 매칭한다.
  if (req.method === 'POST' && (url.startsWith('/v1/messages') || url.startsWith('/v1/chat/completions'))) {
    try {
      const j = JSON.parse(body.toString('utf-8'));
      j.provider = PROVIDER;
      body = Buffer.from(JSON.stringify(j));
    } catch {
      // JSON이 아니면(빈 body 등) 그대로 패스스루
    }
  }
  const headers = { ...req.headers, host: 'openrouter.ai', 'content-length': String(body.length) };
  for (const h of ['accept-encoding', 'connection']) delete headers[h];
  try {
    const up = await fetch('https://openrouter.ai/api' + url, {
      method: req.method,
      headers,
      body: ['GET', 'HEAD'].includes(req.method) ? undefined : body,
    });
    const skip = new Set(['content-encoding', 'content-length', 'transfer-encoding']);
    res.writeHead(up.status, Object.fromEntries([...up.headers].filter(([k]) => !skip.has(k.toLowerCase()))));
    if (up.body) {
      for await (const c of up.body) {
        if (!res.write(c)) await new Promise((r) => res.once('drain', r));
      }
    }
    res.end();
  } catch (e) { res.writeHead(502, { 'content-type': 'text/plain' }); res.end('proxy error: ' + e.message); }
}).listen(PORT, () => console.log('listening on', PORT, JSON.stringify(PROVIDER)));
