"""HTTP Client Module - 通用 HTTP 请求模块

每次请求独立，无持久连接。参数由调用者在调用时提供。

Usage in service code:
    result = modules["http-client"].get(
        "https://api.example.com/users",
        params={"page": 1},
        headers={"Authorization": "Bearer token"},
    )

    result = modules["http-client"].post(
        "https://api.example.com/users",
        json={"name": "alice", "email": "alice@example.com"},
    )
"""

import json as _json_mod
import random
import ssl
import time
import urllib.request
import urllib.parse
import urllib.error


def _create_ssl_context():
    """创建 SSL 上下文，自动加载系统根证书。

    macOS/Linux 上 Python 默认可能不加载系统证书，需要手动创建
    SSL 上下文并通过 certifi 或系统证书路径来加载。
    """
    ctx = ssl.create_default_context()
    # 1. 尝试加载 certifi 提供的证书包（如果已安装）
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
        return ctx
    except ImportError:
        pass
    # 2. 尝试加载系统证书路径
    import platform
    system = platform.system()
    cert_paths = []
    if system == "Darwin":
        cert_paths = [
            "/etc/ssl/cert.pem",
            "/usr/local/etc/openssl@3/cert.pem",
            "/usr/local/etc/openssl@1.1/cert.pem",
            "/opt/homebrew/etc/openssl@3/cert.pem",
            "/opt/homebrew/etc/ca-certificates/cert.pem",
        ]
    elif system == "Linux":
        cert_paths = [
            "/etc/ssl/certs/ca-certificates.crt",       # Debian/Ubuntu
            "/etc/ssl/certs/ca-bundle.crt",             # Debian (alternative)
            "/etc/pki/tls/certs/ca-bundle.crt",         # RHEL/CentOS
            "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",  # RHEL/CentOS 7+
            "/etc/ssl/ca-bundle.pem",                    # OpenSUSE
            "/etc/ssl/cert.pem",                         # Alpine
            "/etc/pki/tls/cacert.pem",                   # Older RHEL
        ]
    for path in cert_paths:
        try:
            ctx.load_verify_locations(path)
            return ctx
        except (OSError, ssl.SSLError):
            continue
    return ctx


def _is_retryable_error(status_code):
    """判断 HTTP 状态码是否属于可重试错误。

    可重试: 无状态码（网络/超时错误）或 5xx（服务端临时故障）。
    不可重试: 4xx（客户端错误）。
    """
    return status_code is None or status_code >= 500


class Module:
    name = "http-client"
    version = "1.0.0"
    description = "Generic HTTP client module for sending HTTP requests"

    def __init__(self):
        self._logger = None
        self._ctx = None

    def on_start(self, ctx):
        self._ctx = ctx
        self._logger = ctx.logger
        ctx.logger.info(f"Module {self.name} started - service: {ctx.service_name}")

    def on_stop(self, ctx):
        ctx.logger.info(f"Module {self.name} stopped")

    def on_config_reload(self, ctx):
        self._ctx = ctx
        self._logger = ctx.logger
        ctx.logger.info(f"Module {self.name} config reloaded")

    def on_error(self, ctx, error):
        ctx.logger.error(f"Module {self.name} error: {error}")

    def _get_retry_config(self, ctx):
        """从模块配置中读取重试参数。"""
        retry_cfg = ctx.module_config.get("retry", {})
        return {
            "enabled": retry_cfg.get("enabled", True),
            "max_retries": retry_cfg.get("max_retries", 3),
            "base_delay": retry_cfg.get("base_delay", 1),
            "max_delay": retry_cfg.get("max_delay", 30),
            "max_total_time": retry_cfg.get("max_total_time", 120),
        }

    def _retry_delay(self, attempt, cfg, deadline):
        """计算指数退避 + jitter 的等待时间，受总时间预算约束。"""
        delay = min(cfg["base_delay"] * (2 ** attempt), cfg["max_delay"])
        jitter = random.uniform(0, delay * 0.5)
        delay = delay + jitter
        remaining = deadline - time.time()
        if remaining <= 0:
            return -1  # 超时
        return min(delay, remaining)

    def get(self, url, *, params=None, headers=None, timeout=30):
        """Send a GET request.

        Args:
            url: Target URL.
            params: Optional query parameters dict.
            headers: Optional request headers dict.
            timeout: Request timeout in seconds, default 30.

        Returns:
            dict: {
                "success": bool,
                "status_code": int | None,
                "headers": dict | None,
                "body": str | None,
                "json": dict/list | None,
                "error": str | None
            }
        """
        return self.request("GET", url, params=params, headers=headers, timeout=timeout)

    def post(self, url, *, data=None, json=None, headers=None, timeout=30):
        """Send a POST request.

        Args:
            url: Target URL.
            data: Optional request body bytes/string.
            json: Optional JSON-serializable data (auto sets Content-Type).
            headers: Optional request headers dict.
            timeout: Request timeout in seconds, default 30.

        Returns:
            dict: Structured response (see request() for format).
        """
        return self.request("POST", url, data=data, json=json, headers=headers, timeout=timeout)

    def put(self, url, *, data=None, json=None, headers=None, timeout=30):
        """Send a PUT request.

        Args:
            url: Target URL.
            data: Optional request body bytes/string.
            json: Optional JSON-serializable data (auto sets Content-Type).
            headers: Optional request headers dict.
            timeout: Request timeout in seconds, default 30.

        Returns:
            dict: Structured response (see request() for format).
        """
        return self.request("PUT", url, data=data, json=json, headers=headers, timeout=timeout)

    def delete(self, url, *, headers=None, timeout=30):
        """Send a DELETE request.

        Args:
            url: Target URL.
            headers: Optional request headers dict.
            timeout: Request timeout in seconds, default 30.

        Returns:
            dict: Structured response (see request() for format).
        """
        return self.request("DELETE", url, headers=headers, timeout=timeout)

    def request(self, method, url, *, params=None, data=None, json=None,
                headers=None, timeout=30):
        """Send a generic HTTP request with automatic retry on transient errors.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.).
            url: Target URL.
            params: Optional query parameters dict (appended to URL).
            data: Optional request body bytes/string.
            json: Optional JSON-serializable data (auto sets Content-Type).
            headers: Optional request headers dict.
            timeout: Request timeout in seconds, default 30.

        Returns:
            dict: {
                "success": bool,
                "status_code": int | None,
                "headers": dict | None,
                "body": str | None,
                "json": dict/list | None,
                "error": str | None,
                "retry_count": int  # 新增：实际重试次数（0=首次成功）
            }
        """
        # 构建请求（只执行一次）
        if params:
            separator = "&" if "?" in url else "?"
            url = url + separator + urllib.parse.urlencode(params)

        body_bytes = None
        if json is not None:
            body_bytes = _json_mod.dumps(json, ensure_ascii=False).encode("utf-8")
            headers = dict(headers) if headers else {}
            headers.setdefault("Content-Type", "application/json; charset=utf-8")
        elif data is not None:
            if isinstance(data, str):
                body_bytes = data.encode("utf-8")
            else:
                body_bytes = data

        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers=headers or {},
            method=method.upper(),
        )
        req.add_header("User-Agent", "PyService-HttpClient/1.0")
        ssl_ctx = _create_ssl_context()

        # 读取重试配置
        cfg = self._get_retry_config(self._ctx)
        max_attempts = cfg["max_retries"] + 1 if cfg["enabled"] else 1
        deadline = time.time() + cfg["max_total_time"] if cfg["enabled"] else 0

        # 重试循环
        for attempt in range(max_attempts):
            try:
                with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as response:
                    status_code = response.status
                    resp_headers = dict(response.headers)
                    resp_body = response.read().decode("utf-8", errors="replace")

                    resp_json = None
                    try:
                        resp_json = _json_mod.loads(resp_body)
                    except (ValueError, _json_mod.JSONDecodeError):
                        pass

                    return {
                        "success": True,
                        "status_code": status_code,
                        "headers": resp_headers,
                        "body": resp_body,
                        "json": resp_json,
                        "error": None,
                        "retry_count": attempt,
                    }

            except urllib.error.HTTPError as e:
                status_code = e.code
                retryable = _is_retryable_error(status_code)
                error_msg = f"HTTP {e.code}: {e.reason}"

                if not retryable or attempt >= max_attempts - 1:
                    resp_body = None
                    resp_json = None
                    resp_headers = None
                    try:
                        resp_headers = dict(e.headers)
                        resp_body = e.read().decode("utf-8", errors="replace")
                        resp_json = _json_mod.loads(resp_body)
                    except Exception:
                        pass
                    return {
                        "success": False,
                        "status_code": status_code,
                        "headers": resp_headers,
                        "body": resp_body,
                        "json": resp_json,
                        "error": error_msg,
                        "retry_count": attempt,
                    }

            except urllib.error.URLError as e:
                error_msg = f"URL error: {e.reason}"
                if attempt >= max_attempts - 1:
                    return {
                        "success": False,
                        "status_code": None,
                        "headers": None,
                        "body": None,
                        "json": None,
                        "error": error_msg,
                        "retry_count": attempt,
                    }

            except TimeoutError:
                error_msg = f"Request timeout after {timeout}s"
                if attempt >= max_attempts - 1:
                    return {
                        "success": False,
                        "status_code": None,
                        "headers": None,
                        "body": None,
                        "json": None,
                        "error": error_msg,
                        "retry_count": attempt,
                    }

            except Exception as e:
                # 通用异常不可重试
                return {
                    "success": False,
                    "status_code": None,
                    "headers": None,
                    "body": None,
                    "json": None,
                    "error": str(e),
                    "retry_count": attempt,
                }

            # 计算等待时间并重试
            if self._logger:
                self._logger.warning(
                    f"[{self.name}] request failed (attempt {attempt + 1}/{max_attempts}), "
                    f"error: {error_msg}"
                )

            delay = self._retry_delay(attempt, cfg, deadline)
            if delay < 0:
                if self._logger:
                    self._logger.warning(
                        f"[{self.name}] retry deadline exceeded ({cfg['max_total_time']}s)"
                    )
                return {
                    "success": False,
                    "status_code": None,
                    "headers": None,
                    "body": None,
                    "json": None,
                    "error": error_msg,
                    "retry_count": attempt,
                }

            if self._logger:
                self._logger.info(f"[{self.name}] retrying in {delay:.1f}s")
            time.sleep(delay)

        # 不应到达此处
        return {
            "success": False,
            "status_code": None,
            "headers": None,
            "body": None,
            "json": None,
            "error": "Unexpected retry loop exit",
            "retry_count": max_attempts,
        }
