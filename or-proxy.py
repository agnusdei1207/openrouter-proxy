import fnmatch
import http.client
import json
import os
import queue
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONFIG = os.environ.get("CONFIG") or "config.json"
PORT = int(os.environ.get("PORT") or 8787)
RETRIES = int(os.environ.get("RETRIES") or 5)  # max retries on transient failures
RETRY_STATUS = {408, 429, 500, 502, 503, 504, 529}
UPSTREAM = "openrouter.ai"

SKIP_REQ = {"accept-encoding", "connection", "keep-alive", "host", "content-length"}
SKIP_RES = {"connection", "keep-alive", "content-encoding", "content-length", "transfer-encoding"}

_cfg = {"mtime": None, "data": {}}
_pool = queue.LifoQueue(maxsize=16)


def load_config():
    # reload when the file changes → edits apply immediately (no restart)
    try:
        mtime = os.path.getmtime(CONFIG)
        if mtime != _cfg["mtime"]:
            with open(CONFIG, encoding="utf-8") as f:
                data = json.load(f)
            _cfg["data"] = data if isinstance(data, dict) else {}
            _cfg["mtime"] = mtime
    except Exception:
        pass  # missing file or broken JSON: keep the last good config
    return _cfg["data"]


def env_rule():
    # MODEL / PROVIDER (comma-separated) / EFFORT env vars: one rule for every request
    rule = {}
    for key in ("model", "provider"):
        items = [s.strip() for s in os.environ.get(key.upper(), "").split(",") if s.strip()]
        if items:
            rule[key] = items
    if os.environ.get("EFFORT"):
        rule["effort"] = os.environ["EFFORT"].strip()
    return rule


ENV_RULE = env_rule()


def rule_for(model):
    if ENV_RULE:
        return ENV_RULE  # env set: config.json is ignored
    # exact key match first, then wildcard keys in file order
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
    # Claude Code puts role:"system" inside messages, which non-Anthropic models reject
    # → move them to the top-level system
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
    # array shorthand: try in order, first to last
    model = rule.get("model")
    if isinstance(model, list):
        rule["model"], rule["models"] = model[0], model
    provider = rule.get("provider")
    if isinstance(provider, str):
        provider = [provider]
    if isinstance(provider, list):
        rule["provider"] = {"order": provider, "allow_fallbacks": False}
    body.update(rule)  # model, provider and any other fields overwrite as-is
    messages_api = path.startswith("/v1/messages")
    if effort:
        # OpenRouter: Anthropic format only honors thinking/output_config, OpenAI format only reasoning
        if not messages_api:
            body["reasoning"] = {"effort": effort}
        elif effort == "none":
            body["thinking"] = {"type": "disabled"}
            body.get("output_config", {}).pop("effort", None)
        else:
            body.setdefault("output_config", {})["effort"] = effort
    if messages_api and "anthropic/" not in str(body.get("model")):
        hoist_system(body)


def new_conn():
    return http.client.HTTPSConnection(UPSTREAM, timeout=600)


def get_conn():
    try:
        return _pool.get_nowait()
    except queue.Empty:
        return new_conn()


def release(conn, up):
    if up.will_close:
        conn.close()
    else:
        put_conn(conn)  # reuse only connections whose response was fully read


def backoff(attempt, up=None):
    try:
        wait = float(up.getheader("retry-after")) if up is not None else 0
    except (TypeError, ValueError):
        wait = 0
    return min(max(wait, 2 ** attempt), 20)  # 1, 2, 4, 8, 16s… (Retry-After wins, capped at 20s)


def put_conn(conn):
    try:
        _pool.put_nowait(conn)
    except queue.Full:
        conn.close()


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.0: close after each response → stream through without Content-Length
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
                pass  # not JSON: pass through unchanged
        headers = {k: v for k, v in self.headers.items() if k.lower() not in SKIP_REQ}
        headers["Host"] = UPSTREAM
        if self.command not in ("GET", "HEAD"):
            headers["Content-Length"] = str(len(body))
        send_body = body if self.command not in ("GET", "HEAD") else None

        conn, up, error = None, None, None
        for attempt in range(RETRIES + 1):
            conn = get_conn() if attempt == 0 else new_conn()
            try:
                conn.request(self.command, "/api" + url, body=send_body, headers=headers)
                up = conn.getresponse()
            except (http.client.HTTPException, OSError) as e:
                # connect failed / pooled connection went stale → retry
                conn.close()
                conn, up, error = None, None, e
                if attempt < RETRIES:
                    print("retry", attempt + 1, url, repr(e), flush=True)
                    time.sleep(0 if attempt == 0 else backoff(attempt - 1))
                continue
            if up.status in RETRY_STATUS and attempt < RETRIES:
                up.read()
                release(conn, up)
                print("retry", attempt + 1, url, up.status, flush=True)
                time.sleep(backoff(attempt, up))
                conn = up = None
                continue
            break

        if up is None:
            # could not connect at all after all retries
            try:
                self.send_response(502, "Bad Gateway")
                self.send_header("content-type", "text/plain")
                self.end_headers()
                self.wfile.write(("proxy error: " + str(error)).encode("utf-8"))
            except Exception:
                pass
            return

        # success, non-retryable error, or last response after retries ran out → forward as-is
        try:
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
            release(conn, up)
        except Exception:
            conn.close()  # client disconnected mid-stream

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _proxy

    def log_message(self, *args):
        pass


class Server(ThreadingHTTPServer):
    # "::" + dual-stack: avoids the IPv4 fallback delay when localhost resolves to IPv6 first
    address_family = socket.AF_INET6
    # SO_REUSEADDR on Windows allows double-binding the same port, so disable it
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()

    def handle_error(self, request, client_address):
        pass  # silence tracebacks when clients drop connections (preconnects etc.)


if __name__ == "__main__":
    server = Server(("::", PORT), Handler)
    server.daemon_threads = True
    print("listening on", PORT, "config:", CONFIG, flush=True)
    server.serve_forever()
