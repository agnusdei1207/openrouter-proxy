import fnmatch
import http.client
import json
import os
import queue
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONFIG = os.environ.get("CONFIG") or "config.json"
PORT = int(os.environ.get("PORT") or 8787)
UPSTREAM = "openrouter.ai"

SKIP_REQ = {"accept-encoding", "connection", "keep-alive", "host", "content-length"}
SKIP_RES = {"connection", "keep-alive", "content-encoding", "content-length", "transfer-encoding"}

_cfg = {"mtime": None, "data": {}}
_pool = queue.LifoQueue(maxsize=16)


def load_config():
    # 파일이 바뀌면 다시 읽는다 → 수정 즉시 반영 (재시작 불필요)
    try:
        mtime = os.path.getmtime(CONFIG)
        if mtime != _cfg["mtime"]:
            with open(CONFIG, encoding="utf-8") as f:
                data = json.load(f)
            _cfg["data"] = data if isinstance(data, dict) else {}
            _cfg["mtime"] = mtime
    except Exception:
        pass  # 파일이 없거나 JSON이 깨졌으면 마지막 정상 설정 유지
    return _cfg["data"]


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


def hoist_system(body):
    # Claude Code는 messages 중간에 role:"system"을 넣는데, Anthropic 외 모델은 이를 거부한다
    # → 최상위 system 으로 옮긴다
    msgs = body.get("messages")
    if not isinstance(msgs, list) or not any(m.get("role") == "system" for m in msgs):
        return
    system = body.get("system") or []
    if isinstance(system, str):
        system = [{"type": "text", "text": system}]
    for m in msgs:
        if m.get("role") == "system":
            c = m.get("content")
            system += [{"type": "text", "text": c}] if isinstance(c, str) else c
    body["system"] = system
    body["messages"] = [m for m in msgs if m.get("role") != "system"]


def apply(body, path):
    rule = rule_for(body.get("model"))
    rule = dict(rule) if isinstance(rule, dict) else {}
    effort = rule.pop("effort", None)
    # 배열 축약형: 앞에서부터 순서대로 시도
    model = rule.get("model")
    if isinstance(model, list):
        rule["model"], rule["models"] = model[0], model
    provider = rule.get("provider")
    if isinstance(provider, str):
        provider = [provider]
    if isinstance(provider, list):
        rule["provider"] = {"order": provider, "allow_fallbacks": False}
    body.update(rule)  # model, provider 등 나머지 필드는 그대로 덮어쓴다
    messages_api = path.startswith("/v1/messages")
    if effort:
        # OpenRouter: Anthropic 형식은 thinking/output_config, OpenAI 형식은 reasoning 만 인식한다
        if not messages_api:
            body["reasoning"] = {"effort": effort}
        elif effort == "none":
            body["thinking"] = {"type": "disabled"}
            body.get("output_config", {}).pop("effort", None)
        else:
            body.setdefault("output_config", {})["effort"] = effort
    if messages_api and "anthropic/" not in str(body.get("model")):
        hoist_system(body)


def get_conn():
    try:
        return _pool.get_nowait()
    except queue.Empty:
        return http.client.HTTPSConnection(UPSTREAM, timeout=600)


def put_conn(conn):
    try:
        _pool.put_nowait(conn)
    except queue.Full:
        conn.close()


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
                if isinstance(j, dict):
                    apply(j, url)
                    body = json.dumps(j).encode("utf-8")
            except Exception:
                pass  # JSON이 아니면 그대로 패스스루
        headers = {k: v for k, v in self.headers.items() if k.lower() not in SKIP_REQ}
        headers["Host"] = UPSTREAM
        if self.command not in ("GET", "HEAD"):
            headers["Content-Length"] = str(len(body))
        send_body = body if self.command not in ("GET", "HEAD") else None

        conn = get_conn()
        try:
            try:
                conn.request(self.command, "/api" + url, body=send_body, headers=headers)
                up = conn.getresponse()
            except (http.client.HTTPException, OSError):
                # 풀에 있던 연결이 끊겼을 수 있음 → 새 연결로 1회 재시도
                conn.close()
                conn = http.client.HTTPSConnection(UPSTREAM, timeout=600)
                conn.request(self.command, "/api" + url, body=send_body, headers=headers)
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
            if up.will_close:
                conn.close()
            else:
                put_conn(conn)  # 응답을 끝까지 읽은 연결만 재사용
            conn = None
        except Exception as e:
            try:
                self.send_response(502, "Bad Gateway")
                self.send_header("content-type", "text/plain")
                self.end_headers()
                self.wfile.write(("proxy error: " + str(e)).encode("utf-8"))
            except Exception:
                pass
        finally:
            if conn is not None:
                conn.close()

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _proxy

    def log_message(self, *args):
        pass


class Server(ThreadingHTTPServer):
    # "::" + dual-stack: localhost(IPv6 우선) 접속 시 IPv4 폴백 지연 제거
    address_family = socket.AF_INET6
    # Windows의 SO_REUSEADDR는 같은 포트 중복 바인딩을 허용하므로 끈다
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()

    def handle_error(self, request, client_address):
        pass  # 클라이언트가 연결을 끊는 경우(사전 연결 등)의 traceback 소음 제거


if __name__ == "__main__":
    server = Server(("::", PORT), Handler)
    server.daemon_threads = True
    print("listening on", PORT, "config:", CONFIG, flush=True)
    server.serve_forever()
