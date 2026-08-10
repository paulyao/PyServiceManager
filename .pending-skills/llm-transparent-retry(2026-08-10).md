# LLM 代理请求透明重试与 Token 刷新

## 适用场景
- LLM 代理服务中 Token 过期（401）时需自动刷新并透明重试，对调用方无感知
- 请求因临时网络问题（超时、5xx）失败时需指数退避重试
- 重试逻辑需与业务逻辑解耦，作为通用中间层封装

## 核心设计

### 1. Token 自动刷新机制
```python
class TokenRefresher:
    """Token 过期时自动获取新 Token 并重试。"""

    def __init__(self, refresh_fn, token_key="api_key"):
        """
        Args:
            refresh_fn: 获取新 Token 的回调函数 () -> str
            token_key: 配置中 Token 字段名
        """
        self._refresh_fn = refresh_fn
        self._token_key = token_key
        self._lock = threading.Lock()

    def refresh(self, current_token):
        """线程安全的 Token 刷新，避免并发重复刷新。"""
        with self._lock:
            new_token = self._refresh_fn()
            if new_token and new_token != current_token:
                log(f"Token 已刷新: {current_token[:8]}... -> {new_token[:8]}...")
                return new_token
            return current_token  # 刷新失败，返回原 Token
```

### 2. 透明重试装饰器
```python
def retry_with_token_refresh(call_fn, token_refresher, max_retries=3, retry_delay=2):
    """带 Token 刷新和指数退避的请求重试。

    Args:
        call_fn: 实际调用函数 (token) -> result
        token_refresher: TokenRefresher 实例
        max_retries: 最大重试次数
        retry_delay: 初始退避间隔（秒），每次翻倍

    Returns:
        调用结果
    """
    token = get_current_token()
    delay = retry_delay

    for attempt in range(max_retries):
        try:
            return call_fn(token)
        except AuthenticationError:
            # 401: Token 过期，刷新后重试（不消耗重试次数）
            log(f"Token 过期(第{attempt+1}次)，正在刷新...", "WARN")
            token = token_refresher.refresh(token)
            continue  # Token 刷新不算一次重试
        except (RateLimitError, APITimeoutError, InternalServerError) as e:
            # 429/500/超时: 指数退避重试
            if attempt < max_retries - 1:
                log(f"请求失败(第{attempt+1}次): {e}，{delay}s 后重试", "WARN")
                time.sleep(delay)
                delay *= 2  # 指数退避
            else:
                raise
    raise RuntimeError("重试次数耗尽")
```

### 3. 错误分类与策略映射
```python
# 错误类型 → 重试策略
RETRY_STRATEGY = {
    "401": "token_refresh",      # Token 过期 → 刷新后重试
    "429": "exponential_backoff", # 限流 → 指数退避
    "500": "exponential_backoff", # 服务端错误 → 指数退避
    "502": "exponential_backoff", # 网关错误 → 指数退避
    "503": "exponential_backoff", # 服务不可用 → 指数退避
    "timeout": "exponential_backoff",  # 超时 → 指数退避
    "400": "no_retry",           # 请求格式错误 → 不重试
    "403": "no_retry",           # 权限不足 → 不重试
    "404": "no_retry",           # 资源不存在 → 不重试
}
```

### 4. 对调用方完全透明
```python
def handle_chat(request_info):
    """POST /chat — 调用方无需关心重试和 Token 刷新。"""
    data, err_resp = _parse_body(request_info)
    if err_resp:
        return err_resp

    try:
        # 重试和 Token 刷新全部在内部完成
        result = retry_with_token_refresh(
            call_fn=lambda token: call_llm_with_token(data["messages"], token),
            token_refresher=token_refresher,
        )
        return _json_response(200, {"success": True, "data": result})
    except Exception as e:
        return _json_response(503, {"success": False, "error": str(e)})
```

## 注意事项
- Token 刷新必须加锁（`threading.Lock`），避免高并发时多个请求同时刷新导致 Token 频繁失效
- Token 刷新失败（返回原 Token）时不应无限重试，应退出重试循环并报错
- 指数退避的初始值建议 2 秒，上限不超过 30 秒（`delay = min(delay * 2, 30)`）
- 401 Token 刷新不计入 `max_retries`，因为它是一个独立的恢复机制
- 日志中必须区分"Token 刷新重试"和"退避重试"，便于排查问题
- 对调用方（HTTP handler）保持接口不变，重试逻辑完全封装在底层
