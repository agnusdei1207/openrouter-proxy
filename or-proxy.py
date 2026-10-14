import http.client
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 기본 provider 고정. 요청 body에 provider가 이미 있으면 클라이언트 선택을 존중한다.
PROVIDER = json.loads(os.environ.get("OR_PROVIDER") or '{"only":["baidu"],"allow_fallbacks":false}')

# 모델별 provider 테이블 (동적 선택). 예:
# OR_ROUTES='{"deepseek/deepseek-v4.1-flash":{"only":["baidu"],"allow_fallbacks":false},"z-ai/glm-4.6":{"only":["z-ai"],"allow_fallbacks":false},"*":{"only":["deepinfra"],"allow_fallbacks":true}}'
# - 요청의 model과 일치하는 항목을 사용, 없으면 "*" 항목, 그것도 없으면 OR_PROVIDER.
# - 모델명 자체는 프록시가 건드리지 않는다. Claude Code의 ANTHROPIC_MODEL이 그대로 전달된다.
try:
    ROUTES = json.loads(os.environ.get("OR_ROUTES") or "{}")
    if not isinstance(ROUTES, dict):
        ROUTES = {}
except Exception:
    ROUTES = {}

PORT = int(os.environ.get("PORT") or 8787)

SKIP_REQ = {"accept-encoding", "connection", "host", "content-length"}
SKIP_RES = {"content-encoding", "content-length", "transfer-encoding"}


def provider_for(model):
    if isinstance(model, str) and model in ROUTES:
        return ROUTES[model]
    if "*" in ROUTES:
        return ROUTES["*"]
    return PROVIDER


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.0: 응답마다 연결 종료 → Content-Length 없이 스트리밍 그대로 전달
    protocol_version = "HTTP/1.0"

    def _proxy(self):
        url = self.path or "/"
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length > 0 else b""
        # Claude Code는 /v1/messages(Anthropic), 직접 코드는 /v1/chat/completions(OpenAI) 사용.
        if self.command == "POST" and (url.startswith("/v1/messages") or url.startswith("/v1/chat/completions")):
            try:
                j = json.loads(body.decode("utf-8") if body else "{}")
                if isinstance(j, dict) and "provider" not in j:
                    j["provider"] = provider_for(j.get("model"))
                    body = json.dumps(j).encode("utf-8")
            except Exception:
                pass  # JSON이 아니면(빈 body 등) 그대로 패스스루
        headers = {k: v for k, v in self.headers.items() if k.lower() not in SKIP_REQ}
        headers["Host"] = "openrouter.ai"
        headers["Content-Length"] = str(len(body))
        conn = http.client.HTTPSConnection("openrouter.ai", timeout=300)
        try:
            conn.request(
                self.command,
                "/api" + url,
                body=body if self.command not in ("GET", "HEAD") else None,
                headers=headers,
            )
            up = conn.getresponse()
            self.send_response(up.status)
            for k, v in up.getheaders():
                if k.lower() not in SKIP_RES:
                    self.send_header(k, v)
            self.end_headers()
            while True:
                chunk = up.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except Exception as e:
            try:
                self.send_response(502, "Bad Gateway")
                self.send_header("content-type", "text/plain")
                self.end_headers()
                self.wfile.write(("proxy error: " + str(e)).encode("utf-8"))
            except Exception:
                pass
        finally:
            conn.close()

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _proxy

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    routes_note = ("routes:" + ",".join(ROUTES)) if ROUTES else "no-routes"
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print("listening on", PORT, json.dumps(provider_for(None)), routes_note, flush=True)
    server.serve_forever()
