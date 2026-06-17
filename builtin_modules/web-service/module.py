"""Web Service Module - 统一 HTTP 服务器模块

为需要展示 Web 页面或 API 的服务提供统一的路由能力。
自动启动独立守护进程运行 HTTP 服务器，多服务共享同一实例，避免重复启动。

Usage in service code:
    web_mod = modules.get("web-service")

    # 注册 HTML 页面
    web_mod.register_page("/", "<html>...</html>")

    # 注册 JSON API（callback 为无参函数，返回 dict）
    web_mod.register_api("/api/data", lambda: {"key": "value"})

    # 注册自定义处理器
    def my_handler(request_info):
        return {"status_code": 200, "content_type": "text/plain", "body": "OK"}
    web_mod.register_handler("/custom", "POST", my_handler)

路由自动添加服务名前缀：注册 "/" 实际路由为 "/{service_name}/"。
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path


# 模块文件所在目录（用于定位 web_daemon.py）
_MODULE_DIR = Path(__file__).resolve().parent


class _CallbackHandler(BaseHTTPRequestHandler):
    """内部回调处理器：接收守护进程转发的请求，执行注册的回调函数。"""

    callbacks = {}  # {path: callback_func}

    def do_POST(self):
        self._handle_callback()

    def _handle_callback(self):
        path = self.path
        callback = self.callbacks.get(path)
        if not callback:
            self._send_response(404, {
                "status_code": 404,
                "content_type": "application/json; charset=utf-8",
                "body": json.dumps({"error": "No callback for path"}),
            })
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"

        try:
            request_data = json.loads(body)
        except json.JSONDecodeError:
            request_data = {"method": "POST", "path": path, "headers": {}, "body": body, "query": {}}

        try:
            response = callback(request_data)
        except Exception as e:
            response = {
                "status_code": 500,
                "content_type": "application/json; charset=utf-8",
                "body": json.dumps({"error": str(e)}),
            }

        self._send_response(
            response.get("status_code", 200),
            response,
            response.get("content_type", "application/json; charset=utf-8"),
            response.get("body", ""),
        )

    def _send_response(self, status_code, response_dict, content_type="application/json; charset=utf-8", body=""):
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        body_bytes = body.encode("utf-8") if isinstance(body, str) else body
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def log_message(self, format, *args):
        pass  # 静默日志


class Module:
    name = "web-service"
    version = "2.0.0"
    description = "Unified HTTP server module with shared daemon process"

    def __init__(self):
        self._logger = None
        self._service_name = ""
        self._url_prefix = ""
        self._host = "0.0.0.0"
        self._port = 8910
        self._data_dir = None
        self._daemon_started = False
        self._api_threads = {}  # {full_path: (thread, stop_event)}
        self._registered_paths = []  # 跟踪已注册路径，供 on_stop 清理
        self._callback_server = None  # 内部回调服务器（POST 路由用）
        self._callback_port = None
        self._callback_handlers = {}  # {full_path: callback_func}

    def on_start(self, ctx):
        """启动守护进程（如果未运行），初始化 URL 前缀。"""
        self._logger = ctx.logger
        self._service_name = ctx.service_name
        self._url_prefix = "/" + ctx.service_name

        server_cfg = ctx.module_config.get("server", {})
        self._port = server_cfg.get("port", 8910)
        self._host = server_cfg.get("host", "0.0.0.0")

        # 数据目录：路由表和 API 数据文件存储位置
        data_dir_str = ctx.module_config.get("data_dir", "")
        if data_dir_str:
            self._data_dir = Path(data_dir_str)
        else:
            self._data_dir = ctx.data_dir / ".web-service"
        self._data_dir.mkdir(parents=True, exist_ok=True)

        # 启动守护进程（如果未运行）
        if not self._is_daemon_running():
            self._start_daemon()
        else:
            ctx.logger.info(
                f"Module {self.name} - service: {self._service_name}, "
                f"daemon already running on {self._host}:{self._port}, "
                f"url_prefix: {self._url_prefix}"
            )

        # 启动内部回调服务器（用于 POST/PUT/DELETE 路由的实时回调）
        self._start_callback_server()

    def on_stop(self, ctx):
        """停止回调服务器、API 后台线程，注销本服务的路由。"""
        # 先停止回调服务器（在注销路由前，确保守护进程还能访问回调）
        if self._callback_server:
            threading.Thread(target=self._callback_server.shutdown, daemon=True).start()
            time.sleep(0.3)
            self._callback_server.server_close()
            self._callback_server = None

        # 停止所有 API 后台线程
        for path, (thread, stop_event) in self._api_threads.items():
            stop_event.set()
            thread.join(timeout=3)
        self._api_threads.clear()

        # 注销本服务的所有路由
        for path in self._registered_paths:
            self._unregister_with_daemon(path)
        self._registered_paths.clear()

        # 清理本服务的 API 数据文件
        service_dir = self._data_dir / self._service_name
        if service_dir.exists():
            for f in service_dir.iterdir():
                f.unlink()
            service_dir.rmdir()

        ctx.logger.info(f"Module {self.name} stopped - service: {self._service_name}")

    def on_config_reload(self, ctx):
        """配置热重载回调。"""
        ctx.logger.info(
            f"Module {self.name} config reloaded - "
            f"note: port/host changes require service restart"
        )

    # ── 公开方法 ──

    def register_handler(self, path, method, callback):
        """注册一个路由处理器。

        GET 请求：callback 在后台线程中定期调用，结果写入数据文件，守护进程从文件读取。
        POST/PUT/DELETE/PATCH 请求：callback 在内部回调服务器中实时执行，
        守护进程将请求代理到回调服务器。

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

        if method_upper == "GET":
            # GET 路由：数据文件模式（后台刷新）
            try:
                initial_request = {
                    "method": "GET", "path": full_path,
                    "headers": {}, "body": None, "query": {},
                }
                initial_response = callback(initial_request)
                data_file = self._write_data_file(full_path, json.dumps(
                    initial_response, ensure_ascii=False
                ))
            except Exception as e:
                if self._logger:
                    self._logger.warning(f"Initial handler call failed: {e}")
                data_file = ""

            route_info = {
                "type": "api",
                "data_file": str(data_file),
                "service": self._service_name,
            }
        else:
            # POST/PUT/DELETE/PATCH 路由：回调服务器模式（实时执行）
            callback_url = f"http://127.0.0.1:{self._callback_port}{full_path}"
            self._callback_handlers[full_path] = callback
            route_info = {
                "type": "callback",
                "callback_url": callback_url,
                "service": self._service_name,
            }

        self._register_with_daemon(full_path, method_upper, route_info)

        if full_path not in self._registered_paths:
            self._registered_paths.append(full_path)

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
        full_path = self._make_full_path(path)
        route_info = {
            "type": "page",
            "html": html,
            "service": self._service_name,
        }
        self._register_with_daemon(full_path, "GET", route_info)

        if full_path not in self._registered_paths:
            self._registered_paths.append(full_path)

        return {
            "success": True,
            "data": {"path": full_path, "method": "GET"},
            "error": None,
        }

    def register_api(self, path, callback):
        """注册一个 JSON API 端点（GET 路由）。

        callback 为无参函数，返回 dict。模块会在后台线程中定期调用 callback
        并将结果写入数据文件，守护进程从文件读取并响应请求。

        Args:
            path: URL 路径（相对）
            callback: 无参函数，返回 dict（将自动 JSON 序列化）

        Returns:
            dict: {"success": bool, "data": dict, "error": str|None}
        """
        full_path = self._make_full_path(path)

        # 立即调用一次 callback 获取初始数据
        try:
            data = callback()
            response_body = json.dumps(
                {"success": True, "data": data, "error": None},
                ensure_ascii=False,
            )
            data_file = self._write_data_file(full_path, response_body)
        except Exception as e:
            if self._logger:
                self._logger.warning(f"Initial API callback failed: {e}")
            data_file = self._get_data_file_path(full_path)
            # 写入空数据占位
            data_file.parent.mkdir(parents=True, exist_ok=True)
            data_file.write_text(
                json.dumps({"success": False, "data": None, "error": "Not ready"}),
                encoding="utf-8",
            )

        route_info = {
            "type": "api",
            "data_file": str(data_file),
            "service": self._service_name,
        }
        self._register_with_daemon(full_path, "GET", route_info)

        if full_path not in self._registered_paths:
            self._registered_paths.append(full_path)

        # 启动后台线程定期刷新数据
        stop_event = threading.Event()
        thread = threading.Thread(
            target=self._api_refresh_loop,
            args=(callback, data_file, stop_event),
            daemon=True,
        )
        thread.start()
        self._api_threads[full_path] = (thread, stop_event)

        return {
            "success": True,
            "data": {"path": full_path, "method": "GET"},
            "error": None,
        }

    def unregister(self, path):
        """移除指定路径的所有路由。

        Args:
            path: URL 路径（相对）

        Returns:
            dict: {"success": bool, "data": dict, "error": str|None}
        """
        full_path = self._make_full_path(path)

        # 停止后台线程
        if full_path in self._api_threads:
            thread, stop_event = self._api_threads.pop(full_path)
            stop_event.set()
            thread.join(timeout=3)

        # 从守护进程注销
        self._unregister_with_daemon(full_path)

        if full_path in self._registered_paths:
            self._registered_paths.remove(full_path)

        return {
            "success": True,
            "data": {"path": full_path, "removed_count": 1},
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
        if path == "/":
            return self._url_prefix
        return self._url_prefix + path

    def _is_daemon_running(self):
        """检查守护进程是否正在运行（PID 文件 + 端口探测）。"""
        pid_file = self._data_dir / "web-service.pid"
        if not pid_file.exists():
            return False

        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)  # 检查进程是否存在
        except (ValueError, OSError):
            # PID 无效或进程不存在，清理旧 PID 文件
            try:
                pid_file.unlink()
            except OSError:
                pass
            return False

        # 进一步验证端口是否在监听
        return self._check_port_open()

    def _check_port_open(self):
        """检查端口是否有进程在监听。"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(("127.0.0.1", self._port))
            sock.close()
            return result == 0
        except Exception:
            return False

    def _start_daemon(self):
        """启动守护进程。"""
        daemon_script = _MODULE_DIR / "web_daemon.py"
        if not daemon_script.exists():
            if self._logger:
                self._logger.error(f"web_daemon.py not found at {daemon_script}")
            return

        try:
            subprocess.Popen(
                [
                    sys.executable,
                    str(daemon_script),
                    "--host", self._host,
                    "--port", str(self._port),
                    "--data-dir", str(self._data_dir),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self._daemon_started = True

            # 等待守护进程就绪
            for _ in range(10):
                time.sleep(0.5)
                if self._check_port_open():
                    if self._logger:
                        self._logger.info(
                            f"Module {self.name} started daemon - service: {self._service_name}, "
                            f"{self._host}:{self._port}, url_prefix: {self._url_prefix}"
                        )
                    return

            if self._logger:
                self._logger.warning("Daemon process started but port not yet open")

        except Exception as e:
            if self._logger:
                self._logger.error(f"Failed to start web daemon: {e}")

    def _start_callback_server(self):
        """启动内部回调服务器，用于 POST/PUT/DELETE 路由的实时回调。"""
        # 绑定到随机端口
        tmp_server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
        self._callback_port = tmp_server.server_address[1]
        tmp_server.server_close()

        # 创建正式的回调服务器
        _CallbackHandler.callbacks = self._callback_handlers
        self._callback_server = HTTPServer(("127.0.0.1", self._callback_port), _CallbackHandler)
        self._callback_server.callbacks = self._callback_handlers
        thread = threading.Thread(target=self._callback_server.serve_forever, daemon=True)
        thread.start()

        if self._logger:
            self._logger.info(
                f"Callback server started on 127.0.0.1:{self._callback_port}"
            )

    def _register_with_daemon(self, path, method, route_info):
        """通过 HTTP 向守护进程注册路由。"""
        url = f"http://127.0.0.1:{self._port}/_register"
        payload = json.dumps({
            "path": path,
            "method": method,
            "route_info": route_info,
        }).encode("utf-8")

        try:
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5)
            if self._logger:
                self._logger.info(f"Route registered: {method} {path}")
        except Exception as e:
            if self._logger:
                self._logger.warning(f"Failed to register route {method} {path}: {e}")

    def _unregister_with_daemon(self, path):
        """通过 HTTP 从守护进程注销路由。"""
        url = f"http://127.0.0.1:{self._port}/_register"
        payload = json.dumps({"path": path}).encode("utf-8")

        try:
            req = urllib.request.Request(
                url, data=payload, method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass  # 守护进程可能已关闭

    def _get_data_file_path(self, full_path):
        """生成 API 数据文件路径。"""
        safe_name = full_path.strip("/").replace("/", "__") or "root"
        return self._data_dir / self._service_name / f"{safe_name}.json"

    def _write_data_file(self, full_path, content):
        """写入 API 数据文件，返回文件路径。"""
        data_file = self._get_data_file_path(full_path)
        data_file.parent.mkdir(parents=True, exist_ok=True)
        data_file.write_text(content, encoding="utf-8")
        return data_file

    def _api_refresh_loop(self, callback, data_file, stop_event):
        """后台线程：定期调用 callback 并更新数据文件。"""
        while not stop_event.is_set():
            stop_event.wait(timeout=5)  # 每 5 秒刷新一次
            if stop_event.is_set():
                break
            try:
                data = callback()
                response_body = json.dumps(
                    {"success": True, "data": data, "error": None},
                    ensure_ascii=False,
                )
                data_file.write_text(response_body, encoding="utf-8")
            except Exception as e:
                if self._logger:
                    self._logger.debug(f"API refresh failed for {data_file.name}: {e}")
