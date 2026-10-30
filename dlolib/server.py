"""
HTTP 接口 + 独立管理页。只听本机（默认 127.0.0.1:3212），只用标准库。

接口（路径照控制台代理的习惯起名，将来想挂到别的程序后面不用改名字）：
  GET  /                           管理页（不要令牌，打开后输入令牌）
  GET  /api/library/health         不要令牌，只说活着
  GET  /api/library/books          有哪几本书
  GET  /api/library/list           ?source=&by=&status=&q=&offset=&limit=
  GET  /api/library/entry          ?id=
  GET  /api/library/stats
  GET  /api/library/search         ?q=&k=&semantic=
  GET  /api/shelf/list             书架（只读）
  POST /api/library/action         {"action": "add|update|mark|remove", "confirm": true, ...}

安全：
  - 除管理页和 health，都要令牌（请求头 x-library-token，或 Authorization: Bearer）。令牌放在 <home>/server.token，
    第一次启动自动生成；不会打印到屏幕。
  - 所有会改书的请求要带 confirm:true，不带就拒绝（防手滑、防别人拿到 GET 链接乱点）。
  - 通过这个接口写进去的条目，来源一律记成「主人手填」（owner）。AI 写入走 MCP（--agent 固定是谁）或直接用 Python。
  - 只认 Host 是 localhost / 127.0.0.1 / [::1] 的请求，防 DNS 重绑定；POST 的 Origin 如果带了，必须是本机。
  - 请求体最大 64KB。
"""
from __future__ import annotations

import hmac
import json
import re
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .store import Library

WEB_DIR = Path(__file__).resolve().parent / "web"
MAX_BODY = 64 * 1024
LOCAL_HOST_RE = re.compile(r"^(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$", re.I)
LOCAL_ORIGIN_RE = re.compile(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$", re.I)
ADD_FIELDS = ("name", "brief", "aliases", "origin", "examples", "era", "refs", "flags", "meaning", "type")


def load_or_create_token(lib: Library) -> str:
    f = lib.home / "server.token"
    if f.exists():
        tok = f.read_text(encoding="utf-8").strip()
        if len(tok) >= 24:
            return tok
    tok = secrets.token_hex(24)
    f.write_text(tok, encoding="utf-8")
    return tok


class Api:
    """和 HTTP 无关的处理逻辑，测试可以直接调。返回 (状态码, 对象)。"""

    def __init__(self, lib: Library):
        self.lib = lib

    def _book(self, params):
        return self.lib.book((params.get("book") or [""])[0])

    def get(self, path: str, params: dict):
        p = path.rstrip("/")
        if p == "/api/library/health":
            return 200, {"ok": True}
        if p == "/api/library/books":
            return 200, {"books": self.lib.book_list(), "owner": self.lib.owner_name, "agents": self.lib.agents,
                         "labels": {k: self.lib.label(k) for k in ("seed", "asked", "self", "owner")}}
        if p == "/api/shelf/list":
            return 200, {"books": self.lib.shelf().list(), "log": self.lib.shelf().reading_log(7)}
        try:
            book = self._book(params)
        except KeyError as e:
            return 404, {"ok": False, "error": str(e.args[0])}
        one = lambda k, d="": (params.get(k) or [d])[0]  # noqa: E731
        if p == "/api/library/list":
            return 200, book.list(source=one("source"), by=one("by"), status=one("status"), q=one("q"),
                                  offset=max(0, int(one("offset", "0") or 0)),
                                  limit=max(1, min(200, int(one("limit", "50") or 50))))
        if p == "/api/library/entry":
            e = book.get(one("id"))
            return (200, {"ok": True, "entry": e}) if e else (404, {"ok": False, "error": "没有这一条"})
        if p == "/api/library/stats":
            return 200, book.stats()
        if p == "/api/library/search":
            res = book.search(one("q"), k=max(1, min(20, int(one("k", "5") or 5))),
                              semantic=one("semantic") in ("1", "true"), include_hidden=True)
            return 200, {"results": res}
        return 404, {"ok": False, "error": "没有这个接口"}

    def post(self, path: str, body: dict):
        if path.rstrip("/") != "/api/library/action":
            return 404, {"ok": False, "error": "没有这个接口"}
        if body.get("confirm") is not True:
            return 400, {"ok": False, "error": "要改书的话请带上 confirm:true"}
        try:
            book = self.lib.book(str(body.get("book") or ""))
        except KeyError as e:
            return 404, {"ok": False, "error": str(e.args[0])}
        action = body.get("action")
        if action == "add":
            kw = {k: body[k] for k in ADD_FIELDS if k in body}
            res = book.add(source={"kind": "owner", "by": "owner"}, **kw) if {"name", "brief"} <= set(kw) else \
                {"ok": False, "error": "要写名字（name）和一句意思（brief）"}
        elif action == "update":
            fields = body.get("fields") if isinstance(body.get("fields"), dict) else {}
            res = book.update(str(body.get("id") or ""), fields, "owner")
        elif action == "mark":
            res = book.mark(str(body.get("id") or ""), "owner", status=body.get("status"), hidden=body.get("hidden"))
        elif action == "remove":
            res = book.remove(str(body.get("id") or ""), "owner")
        else:
            return 400, {"ok": False, "error": "action 只能是 add / update / mark / remove"}
        return (200 if res.get("ok") else 400), res


def make_handler(api: Api, token: str):
    page = (WEB_DIR / "admin.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        server_version = "dlolib"

        def log_message(self, fmt, *args):      # 不往屏幕刷访问日志（里面可能带查询词）
            pass

        def _send(self, code, obj=None, raw=None, ctype="application/json; charset=utf-8"):
            data = raw if raw is not None else json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if raw is not None:
                self.send_header("Content-Security-Policy",
                                 "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                                 "connect-src 'self'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(data)

        def _authed(self) -> bool:
            if not LOCAL_HOST_RE.match(self.headers.get("Host", "")):
                self._send(403, {"ok": False, "error": "只接受本机访问"})
                return False
            given = self.headers.get("x-library-token", "")
            auth = self.headers.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                given = auth[7:].strip()
            if not given or not hmac.compare_digest(given.encode(), token.encode()):
                self._send(401, {"ok": False, "error": "需要令牌（x-library-token）"})
                return False
            return True

        def do_GET(self):
            u = urlparse(self.path)
            if u.path in ("/", "/index.html", "/admin.html"):
                if not LOCAL_HOST_RE.match(self.headers.get("Host", "")):
                    return self._send(403, {"ok": False, "error": "只接受本机访问"})
                return self._send(200, raw=page, ctype="text/html; charset=utf-8")
            if u.path.rstrip("/") == "/api/library/health":
                return self._send(200, {"ok": True})
            if not self._authed():
                return
            try:
                code, obj = api.get(u.path, parse_qs(u.query))
            except (ValueError, TypeError) as e:
                code, obj = 400, {"ok": False, "error": f"参数不对：{e}"}
            except Exception as e:                      # noqa: BLE001  接口不能因为一次出错就挂掉
                code, obj = 500, {"ok": False, "error": f"出错了：{type(e).__name__}"}
            self._send(code, obj)

        def do_POST(self):
            if not self._authed():
                return
            origin = self.headers.get("Origin")
            if origin and not LOCAL_ORIGIN_RE.match(origin):
                return self._send(403, {"ok": False, "error": "不接受别的网站发来的请求"})
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                n = -1
            if n < 0 or n > MAX_BODY:
                return self._send(413, {"ok": False, "error": "请求太大了"})
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("要是一个 JSON 对象")
            except ValueError as e:
                return self._send(400, {"ok": False, "error": f"请求内容读不懂：{e}"})
            try:
                code, obj = api.post(urlparse(self.path).path, body)
            except TimeoutError as e:
                code, obj = 503, {"ok": False, "error": str(e)}
            except Exception as e:                      # noqa: BLE001
                code, obj = 500, {"ok": False, "error": f"出错了：{type(e).__name__}"}
            self._send(code, obj)

    return Handler


def make_server(lib: Library, host: str = "127.0.0.1", port: int = 3212, token: str | None = None):
    token = token or load_or_create_token(lib)
    srv = ThreadingHTTPServer((host, port), make_handler(Api(lib), token))
    srv.daemon_threads = True
    return srv, token


def serve(lib: Library, host: str = "127.0.0.1", port: int = 3212) -> int:
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("为了安全，只能绑在本机地址（127.0.0.1）上。", file=sys.stderr)
        return 2
    srv, _ = make_server(lib, host, port)
    print(f"图书馆管理页：http://{host}:{port}/   令牌在 {lib.home / 'server.token'}（打开页面后粘贴进去）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
