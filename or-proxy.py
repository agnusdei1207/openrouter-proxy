import fnmatch
import http.client
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONFIG = os.environ.get("CONFIG") or "config.json"
PORT = int(os.environ.get("PORT") or 8787)

SKIP_REQ = {"accept-encoding", "connection", "host", "content-length"}
SKIP_RES = {"connection", "keep-alive", "content-encoding", "content-length", "transfer-encoding"}


def load_config():
    # 요청마다 읽는다 → config.json 수정 즉시 반영 (재시작 불필요)
    try:
        with open(CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def rule_for(model):
    # 정확히 일치하는 키 우선, 그다음 와일드카드 키를 파일 순서대로
    cfg = load_config()
    if not isinstance(model, str):
        model = ""
    if model in cfg:
        return cfg[model]
    for pattern, rule in cfg.items():
        if fnmatch.fnmatchcase(model, pattern):
            return rule
    return {}


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
        if self.command == "POST" and body:
            try:
                j = json.loads(body)
                rule = rule_for(j.get("model"))
                if isinstance(rule, dict) and rule:
                    j.update(rule)  # 규칙의 필드(model, provider, reasoning 등)로 덮어쓴다
                    body = json.dumps(j).encode("utf-8")
            except Exception:
                pass  # JSON이 아니면 그대로 패스스루
        headers = {k: v for k, v in self.headers.items() if k.lower() not in SKIP_REQ}
        headers["Host"] = "openrouter.ai"
        headers["Content-Length"] = str(len(body))
        conn = http.client.HTTPSConnection("openrouter.ai", timeout=600)
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
                chunk = up.read1(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
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
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print("listening on", PORT, "config:", CONFIG, flush=True)
    server.serve_forever()
