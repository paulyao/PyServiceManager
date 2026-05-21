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
import ssl
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


class Module:
    name = "http-client"
    version = "1.0.0"
    description = "Generic HTTP client module for sending HTTP requests"

    def on_start(self, ctx):
        ctx.logger.info(f"Module {self.name} started - service: {ctx.service_name}")

    def on_stop(self, ctx):
        ctx.logger.info(f"Module {self.name} stopped")

    def on_config_reload(self, ctx):
        ctx.logger.info(f"Module {self.name} config reloaded")

    def on_error(self, ctx, error):
        ctx.logger.error(f"Module {self.name} error: {error}")

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
        """Send a generic HTTP request.

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
                "error": str | None
            }
        """
        try:
            # Build URL with query parameters
            if params:
                separator = "&" if "?" in url else "?"
                url = url + separator + urllib.parse.urlencode(params)

            # Prepare request body
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

            # Build request
            req = urllib.request.Request(
                url,
                data=body_bytes,
                headers=headers or {},
                method=method.upper(),
            )
            req.add_header("User-Agent", "PyService-HttpClient/1.0")

            # Send request
            ssl_ctx = _create_ssl_context()
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as response:
                status_code = response.status
                resp_headers = dict(response.headers)
                resp_body = response.read().decode("utf-8", errors="replace")

                # Try to parse JSON response
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
                }

        except urllib.error.HTTPError as e:
            # HTTP error responses (4xx, 5xx)
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
                "status_code": e.code,
                "headers": resp_headers,
                "body": resp_body,
                "json": resp_json,
                "error": f"HTTP {e.code}: {e.reason}",
            }

        except urllib.error.URLError as e:
            return {
                "success": False,
                "status_code": None,
                "headers": None,
                "body": None,
                "json": None,
                "error": f"URL error: {e.reason}",
            }

        except TimeoutError:
            return {
                "success": False,
                "status_code": None,
                "headers": None,
                "body": None,
                "json": None,
                "error": f"Request timeout after {timeout}s",
            }

        except Exception as e:
            return {
                "success": False,
                "status_code": None,
                "headers": None,
                "body": None,
                "json": None,
                "error": str(e),
            }
