"""Web Service Module - 统一 HTTP 服务器模块

为需要展示 Web 页面或 API 的服务提供统一的路由能力，避免每个服务自行启动 HTTP 服务器。

Usage in service code:
    web_mod = modules.get("web-service")

    # 注册 HTML 页面
    web_mod.register_page("/", "<html>...</html>")

    # 注册 JSON API
    web_mod.register_api("/api/data", lambda: {"key": "value"})

    # 注册自定义处理器
    def my_handler(request_info):
        return {"status_code": 200, "content_type": "text/plain", "body": "OK"}
    web_mod.register_handler("/custom", "POST", my_handler)

路由自动添加服务名前缀：注册 "/" 实际路由为 "/{service_name}/"。
"""

import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs


class _WebServiceHandler(BaseHTTPRequestHandler):
    """通用 HTTP 请求处理器，将请求分派到模块路由表。"""

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_PATCH(self):
        self._handle("PATCH")

    def _handle(self, method):
        """解析请求并调用模块的 _dispatch 方法。"""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        # 读取请求体
        body = None
        content_length = self.headers.get("Content-Length")
        if content_length:
            try:
                body = self.rfile.read(int(content_length)).decode("utf-8", errors="replace")
            except Exception:
                body = None

        headers = dict(self.headers)

        response = self.server.module._dispatch(method, path, headers, body, query)

        status_code = response.get("status_code", 200)
        content_type = response.get("content_type", "text/plain")
        body_content = response.get("body", "")

        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        if isinstance(body_content, str):
            body_bytes = body_content.encode("utf-8")
        else:
            body_bytes = body_content
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def log_message(self, format, *args):
        """重定向日志到模块 logger。"""
        if self.server.module._logger:
            self.server.module._logger.debug(f"HTTP {args[0] if args else ''}")


class Module:
    name = "web-service"
    version = "1.0.0"
    description = "Unified HTTP server module for serving web pages and APIs"

    def __init__(self):
        self._httpd = None
        self._server_thread = None
        self._routes = {}  # {(path, method): callback}
        self._routes_lock = threading.Lock()
        self._logger = None
        self._service_name = ""
        self._url_prefix = ""
        self._host = "0.0.0.0"
        self._port = 8910

    def on_start(self, ctx):
        """启动 HTTP 服务器，从 ctx.service_name 生成 URL 前缀。"""
        self._logger = ctx.logger
        self._service_name = ctx.service_name
        self._url_prefix = "/" + ctx.service_name

        server_cfg = ctx.module_config.get("server", {})
        self._port = server_cfg.get("port", 8910)
        self._host = server_cfg.get("host", "0.0.0.0")

        try:
            self._httpd = HTTPServer((self._host, self._port), _WebServiceHandler)
            self._httpd.module = self  # 让 handler 能访问模块实例
            self._server_thread = threading.Thread(
                target=self._httpd.serve_forever, daemon=True
            )
            self._server_thread.start()
            ctx.logger.info(
                f"Module {self.name} started - service: {self._service_name}, "
                f"listening on {self._host}:{self._port}, "
                f"url_prefix: {self._url_prefix}"
            )
        except OSError as e:
            ctx.logger.error(
                f"Module {self.name} failed to start HTTP server on "
                f"{self._host}:{self._port}: {e}"
            )
            self._httpd = None

    def on_stop(self, ctx):
        """优雅关闭 HTTP 服务器。"""
        if self._httpd:
            threading.Thread(target=self._httpd.shutdown, daemon=True).start()
            import time
            time.sleep(0.5)
            self._httpd.server_close()
            ctx.logger.info(f"Module {self.name} HTTP server stopped")

    def on_config_reload(self, ctx):
        """配置热重载回调。"""
        ctx.logger.info(
            f"Module {self.name} config reloaded - "
            f"note: port/host changes require service restart"
        )

    # ── 公开方法 ──

    def register_handler(self, path, method, callback):
        """注册一个路由处理器。

        Args:
            path: URL 路径（相对），如 "/" 或 "/api/data"
            method: HTTP 方法，如 "GET"、"POST"
            callback: 处理函数，签名 callback(request_info) -> response_dict
                request_info: {"method", "path", "headers", "body", "query"}
                response_dict: {"status_code": int, "content_type": str, "body": str}

        Returns:
            dict: {"success": bool, "data": dict, "error": str|None}
        """
        full_path = self._make_full_path(path)
        method_upper = method.upper()

        with self._routes_lock:
            self._routes[(full_path, method_upper)] = callback

        if self._logger:
            self._logger.info(f"Route registered: {method_upper} {full_path}")

        return {
            "success": True,
            "data": {"path": full_path, "method": method_upper},
            "error": None,
        }

    def register_page(self, path, html):
        """注册一个静态 HTML 页面（GET 路由）。

        Args:
            path: URL 路径（相对）
            html: HTML 字符串内容

        Returns:
            dict: {"success": bool, "data": dict, "error": str|None}
        """
        def _page_handler(request_info):
            return {
                "status_code": 200,
                "content_type": "text/html; charset=utf-8",
                "body": html,
            }

        return self.register_handler(path, "GET", _page_handler)

    def register_api(self, path, callback):
        """注册一个 JSON API 端点（GET 路由）。

        Args:
            path: URL 路径（相对）
            callback: 无参函数，返回 dict（将自动 JSON 序列化）

        Returns:
            dict: {"success": bool, "data": dict, "error": str|None}
        """
        def _api_handler(request_info):
            try:
                data = callback()
                response_body = json.dumps(
                    {"success": True, "data": data, "error": None},
                    ensure_ascii=False,
                )
                return {
                    "status_code": 200,
                    "content_type": "application/json; charset=utf-8",
                    "body": response_body,
                }
            except Exception as e:
                error_body = json.dumps(
                    {"success": False, "data": None, "error": str(e)},
                    ensure_ascii=False,
                )
                return {
                    "status_code": 500,
                    "content_type": "application/json; charset=utf-8",
                    "body": error_body,
                }

        return self.register_handler(path, "GET", _api_handler)

    def unregister(self, path):
        """移除指定路径的所有路由。

        Args:
            path: URL 路径（相对）

        Returns:
            dict: {"success": bool, "data": dict, "error": str|None}
        """
        full_path = self._make_full_path(path)
        removed = 0

        with self._routes_lock:
            keys_to_remove = [k for k in self._routes if k[0] == full_path]
            for key in keys_to_remove:
                del self._routes[key]
                removed += 1

        if self._logger:
            self._logger.info(f"Route unregistered: {full_path} ({removed} methods)")

        return {
            "success": True,
            "data": {"path": full_path, "removed_count": removed},
            "error": None,
        }

    def get_port(self):
        """返回当前监听端口和 URL 前缀信息。

        Returns:
            dict: {"success": bool, "data": dict, "error": str|None}
        """
        base_url = f"http://{self._host}:{self._port}{self._url_prefix}"
        return {
            "success": True,
            "data": {
                "port": self._port,
                "host": self._host,
                "url_prefix": self._url_prefix,
                "base_url": base_url,
            },
            "error": None,
        }

    # ── 内部方法 ──

    def _make_full_path(self, path):
        """拼接 URL 前缀和路径。"""
        if not path.startswith("/"):
            path = "/" + path
        # 避免双斜杠：前缀 "/" + path "/" -> "/"
        if path == "/":
            return self._url_prefix
        return self._url_prefix + path

    def _dispatch(self, method, path, headers, body, query):
        """路由查找与执行。锁内查找 callback，释放锁后执行。"""
        with self._routes_lock:
            callback = self._routes.get((path, method))

        if callback is None:
            # 根路径自动生成索引页
            if path == "/" and method == "GET":
                return self._auto_index()
            return {
                "status_code": 404,
                "content_type": "application/json; charset=utf-8",
                "body": json.dumps({"error": "Not Found", "path": path}),
            }

        try:
            request_info = {
                "method": method,
                "path": path,
                "headers": headers,
                "body": body,
                "query": query,
            }
            return callback(request_info)
        except Exception as e:
            if self._logger:
                self._logger.error(f"Handler error for {method} {path}: {e}")
            return {
                "status_code": 500,
                "content_type": "application/json; charset=utf-8",
                "body": json.dumps({"error": str(e)}),
            }

    def _auto_index(self):
        """生成自动索引页，列出所有已注册路由。"""
        with self._routes_lock:
            routes = list(self._routes.keys())

        # 按前缀分组
        groups = {}
        for path, method in routes:
            # 提取服务前缀（第一段路径）
            parts = path.strip("/").split("/")
            prefix = "/" + parts[0] if parts[0] else "/"
            if prefix not in groups:
                groups[prefix] = []
            groups[prefix].append((path, method))

        rows = []
        for prefix, route_list in sorted(groups.items()):
            rows.append(f'<tr><td colspan="2"><strong>{prefix}</strong></td></tr>')
            for path, method in sorted(route_list):
                rows.append(
                    f'<tr><td><a href="{path}">{path}</a></td>'
                    f'<td><span class="method">{method}</span></td></tr>'
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
.method {{ background: #dbeafe; color: #1e40af; padding: 2px 8px; border-radius: 4px; font-size: 12px; }}
</style>
</head>
<body>
<h1>Web Service Routes</h1>
<p>Service: <strong>{self._service_name}</strong> | Prefix: <code>{self._url_prefix}</code></p>
<table>
<thead><tr><th>Path</th><th>Method</th></tr></thead>
<tbody>
{"".join(rows) if rows else '<tr><td colspan="2">No routes registered</td></tr>'}
</tbody>
</table>
</body>
</html>
"""
        return {
            "status_code": 200,
            "content_type": "text/html; charset=utf-8",
            "body": html,
        }
