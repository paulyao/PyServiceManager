"""Web Service Daemon - 独立 HTTP 守护进程

由 web-service 模块自动启动，多服务共享一个 HTTP 服务器实例。
通过内部注册 API 接收各服务的路由定义，按路由表分派请求。

路由类型：
- page: 静态 HTML，直接返回
- api: JSON API，从数据文件读取最新响应

用法：
    python web_daemon.py --host 0.0.0.0 --port 8910 --data-dir /path/to/data
"""

import argparse
import json
import os
import signal
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path


_ROUTES_LOCK = threading.Lock()
_ROUTES = {}  # {path: {method: route_info}}
_LOGGER = None


def _log(msg, level="INFO"):
    if _LOGGER:
        getattr(_LOGGER, level.lower(), _LOGGER.info)(msg)
    else:
        print(f"[{level}] {msg}")


def _save_routes(data_dir):
    """持久化路由表到磁盘。"""
    routes_file = data_dir / "routes.json"
    try:
        serializable = {}
        for path, methods in _ROUTES.items():
            serializable[path] = {}
            for method, info in methods.items():
                serializable[path][method] = info
        routes_file.write_text(json.dumps(serializable, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        _log(f"Failed to save routing table: {e}", "ERROR")


def _load_routes(data_dir):
    """从磁盘加载路由表。"""
    global _ROUTES
    routes_file = data_dir / "routes.json"
    if routes_file.exists():
        try:
            _ROUTES = json.loads(routes_file.read_text(encoding="utf-8"))
        except Exception as e:
            _log(f"Failed to load routing table: {e}", "ERROR")
            _ROUTES = {}


class DaemonHandler(BaseHTTPRequestHandler):
    """HTTP 处理器：注册 API + 路由分派。"""

    data_dir = None

    def do_GET(self):
        """服务已注册的路由，"""
        path = self.path.split("?")[0].rstrip("/") or "/"

        with _ROUTES_LOCK:
            route_entry = _ROUTES.get(path)
            if route_entry:
                route_info = route_entry.get("GET")
            else:
                route_info = None

        if route_info:
            self._serve_route(route_info)
        elif path == "/":
            self._serve_auto_index()
        else:
            self._send_json(404, {"error": "Not Found", "path": path})

    def do_POST(self):
        """处理 POST 请求：注册 API（localhost）或代理到回调服务器。"""
        path = self.path.split("?")[0].rstrip("/") or "/"

        # 内部注册 API：仅限 localhost
        if path in ("/_register", "/_unregister"):
            self._handle_registration(path)
            return

        # 检查是否为已注册的 POST 路由
        with _ROUTES_LOCK:
            route_entry = _ROUTES.get(path)
            if route_entry:
                route_info = route_entry.get("POST")
            else:
                route_info = None

        if route_info and route_info.get("callback_url"):
            self._proxy_to_callback(route_info["callback_url"], "POST", path)
        else:
            self._send_json(404, {"error": "Not Found", "path": path})

    def do_PUT(self):
        self._proxy_method("PUT")

    def do_DELETE(self):
        """DELETE 方式注销路由，或代理 DELETE 请求。"""
        path = self.path.split("?")[0]
        if path.startswith("/_unregister/"):
            route_path = "/" + path[len("/_unregister/"):]
            removed = 0
            with _ROUTES_LOCK:
                if route_path in _ROUTES:
                    removed = len(_ROUTES[route_path])
                    del _ROUTES[route_path]
                    _save_routes(self.data_dir)
            self._send_json(200, {"success": True, "removed_count": removed})
            return
        self._proxy_method("DELETE")

    def do_PATCH(self):
        self._proxy_method("PATCH")

    def _proxy_method(self, method):
        """代理 PUT/DELETE/PATCH 请求到回调服务器。"""
        path = self.path.split("?")[0].rstrip("/") or "/"
        with _ROUTES_LOCK:
            route_entry = _ROUTES.get(path)
            if route_entry:
                route_info = route_entry.get(method)
            else:
                route_info = None
        if route_info and route_info.get("callback_url"):
            self._proxy_to_callback(route_info["callback_url"], method, path)
        else:
            self._send_json(404, {"error": "Not Found", "path": path})

    def _handle_registration(self, path):
        """处理路由注册/注销（仅限 localhost）。"""
        remote = self.client_address[0]
        if remote not in ("127.0.0.1", "::1", "localhost"):
            self._send_json(403, {"error": "Registration API is localhost only"})
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"

        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            self._send_json(400, {"error": f"Invalid JSON: {e}"})
            return

        if path == "/_register":
            route_path = data.get("path", "")
            method = data.get("method", "GET").upper()
            route_info = data.get("route_info", {})

            with _ROUTES_LOCK:
                if route_path not in _ROUTES:
                    _ROUTES[route_path] = {}
                _ROUTES[route_path][method] = route_info
                _save_routes(self.data_dir)

            _log(f"Route registered: {method} {route_path}")
            self._send_json(200, {"success": True, "path": route_path, "method": method})

        elif path == "/_unregister":
            route_path = data.get("path", "")
            removed = 0
            with _ROUTES_LOCK:
                if route_path in _ROUTES:
                    removed = len(_ROUTES[route_path])
                    del _ROUTES[route_path]
                    _save_routes(self.data_dir)

            _log(f"Route unregistered: {route_path} ({removed} methods)")
            self._send_json(200, {"success": True, "path": route_path, "removed_count": removed})

    def _proxy_to_callback(self, callback_url, method, path):
        """代理请求到服务的回调服务器。"""
        import urllib.request
        import urllib.error
        from urllib.parse import urlparse, parse_qs

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        payload = json.dumps({
            "method": method,
            "path": path,
            "headers": dict(self.headers),
            "body": body.decode("utf-8", errors="replace") if body else "",
            "query": query,
        }).encode("utf-8")

        try:
            req = urllib.request.Request(
                callback_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp_body = resp.read()
                resp_ct = resp.headers.get("Content-Type", "application/json")
                self.send_response(resp.status)
                self.send_header("Content-Type", resp_ct)
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)
        except Exception as e:
            _log(f"Callback proxy error for {method} {path}: {e}", "ERROR")
            self._send_json(502, {"error": f"Callback server error: {e}"})

    def _serve_route(self, route_info):
        """根据路由类型返回响应。"""
        route_type = route_info.get("type", "page")

        if route_type == "page":
            html = route_info.get("html", "")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            body = html.encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif route_type == "api":
            data_file = route_info.get("data_file", "")
            if data_file and os.path.exists(data_file):
                try:
                    content = Path(data_file).read_text(encoding="utf-8")
                    body = content.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                except Exception as e:
                    _log(f"Failed to read data file {data_file}: {e}", "ERROR")
            self._send_json(503, {"error": "Data not available yet"})

        else:
            self._send_json(500, {"error": f"Unknown route type: {route_type}"})

    def _serve_auto_index(self):
        """自动生成索引页，按服务前缀分组列出所有路由。"""
        with _ROUTES_LOCK:
            routes_snapshot = {p: list(m.keys()) for p, m in _ROUTES.items()}

        groups = {}
        for path, methods in routes_snapshot.items():
            parts = path.strip("/").split("/")
            prefix = "/" + parts[0] if parts[0] else "/"
            groups.setdefault(prefix, []).append((path, methods))

        rows = []
        for prefix, route_list in sorted(groups.items()):
            rows.append(f'<tr><td colspan="2"><strong>{prefix}</strong></td></tr>')
            for path, methods in sorted(route_list):
                method_badges = " ".join(
                    f'<span class="method">{m}</span>' for m in sorted(methods)
                )
                rows.append(
                    f'<tr><td><a href="{path}">{path}</a></td>'
                    f"<td>{method_badges}</td></tr>"
                )

        html = f"""\
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>Web Service Routes</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; }}
h1 {{ color: #1f2937; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #e5e7eb; }}
th {{ background: #f9fafb; font-weight: 600; }}
a {{ color: #3b82f6; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
.method {{ background: #dbeafe; color: #1e40af; padding: 2px 8px; border-radius: 4px; font-size: 12px;
  display: inline-block; margin-right: 4px; }}
</style>
</head>
<body>
<h1>Web Service Routes</h1>
<p>Daemon PID: <strong>{os.getpid()}</strong> |
   Total routes: <strong>{sum(len(m) for m in routes_snapshot.values())}</strong></p>
<table>
<thead><tr><th>Path</th><th>Methods</th></tr></thead>
<tbody>
{"".join(rows) if rows else '<tr><td colspan="2">No routes registered</td></tr>'}
</tbody>
</table>
</body>
</html>
"""
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code, data):
        """发送 JSON 响应。"""
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        _log(f"HTTP {args[0] if args else ''}", "DEBUG")


def main():
    parser = argparse.ArgumentParser(description="Web Service Daemon")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8910)
    parser.add_argument("--data-dir", required=True, help="数据目录（存储路由表和 API 数据文件）")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    DaemonHandler.data_dir = data_dir

    # 加载已有路由表
    _load_routes(data_dir)

    # 写入 PID 文件
    pid_file = data_dir / "web-service.pid"
    pid_file.write_text(str(os.getpid()))

    httpd = HTTPServer((args.host, args.port), DaemonHandler)

    def shutdown_handler(signum, frame):
        _log("Daemon shutting down")
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    _log(f"Web Service Daemon started on {args.host}:{args.port} (PID {os.getpid()})")

    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
        if pid_file.exists():
            pid_file.unlink()
        _log("Daemon stopped")


if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _LOGGER = logging.getLogger("web-daemon")
    main()
