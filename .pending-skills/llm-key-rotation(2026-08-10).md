# LLM API Key 双层轮询与自动配额管理

## 适用场景
- LLM 代理服务需支持多个 API Key，部分无额度限制（企业/包月），部分有额度限制（按量付费）
- 需要在 Key 之间均匀分配请求负载，自动检测额度耗尽并摘除不可用 Key
- 优先消耗无限额度 Key，有额度 Key 作为补充池

## 核心设计

### 1. 双层 Key 池配置（config.toml）
```toml
[llm]
# 无限额度 Key 池（企业/包月 Key，优先使用）
unlimited_keys = ["sk-aaa...", "sk-bbb..."]
# 有额度限制 Key 池（按量付费，备选使用）
limited_keys = ["sk-ccc...", "sk-ddd..."]
# 配额检查间隔（秒），定期查询剩余额度
quota_check_interval = 3600
```

### 2. 双层轮询调度策略
```python
import itertools

class KeyPool:
    def __init__(self, unlimited_keys, limited_keys):
        # 两层独立的轮询迭代器
        self._unlimited_cycle = itertools.cycle(unlimited_keys) if unlimited_keys else None
        self._limited_cycle = itertools.cycle(limited_keys) if limited_keys else None
        self._quota_map = {}  # key -> {"remaining": float, "checked_at": timestamp}

    def next_key(self):
        """优先返回无限额度 Key，全部不可用时回退到有限额度 Key。"""
        # 策略 1：优先无限额度池
        if self._unlimited_cycle:
            key = next(self._unlimited_cycle)
            if self._is_available(key):
                return key
        # 策略 2：回退到有限额度池
        if self._limited_cycle:
            for _ in range(len(self._limited_keys)):
                key = next(self._limited_cycle)
                if self._is_available(key) and self._has_quota(key):
                    return key
        raise RuntimeError("所有 API Key 均不可用或额度耗尽")

    def _is_available(self, key):
        """检查 Key 是否被健康检查标记为不可用。"""
        return not self._unhealthy.get(key, False)

    def _has_quota(self, key):
        """检查有限额度 Key 是否仍有剩余配额。"""
        info = self._quota_map.get(key, {})
        return info.get("remaining", 0) > 0
```

### 3. 定时额度查询
```python
def check_quota(self, key, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"):
    """调用平台额度查询接口，更新 Key 的剩余配额。"""
    from openai import OpenAI
    client = OpenAI(api_key=key, base_url=base_url)
    # 通过平台 API 或 Dashboard 查询剩余额度
    # 不同平台接口不同，需适配
    remaining = query_platform_quota(key)
    self._quota_map[key] = {
        "remaining": remaining,
        "checked_at": time.time(),
    }
    if remaining <= 0:
        log(f"Key {key[:8]}... 额度已耗尽，自动摘除", "WARN")
```

### 4. 健康检查与自动恢复
```python
def mark_unhealthy(self, key, reason=""):
    """标记 Key 为不可用（如 429/401 错误）。"""
    self._unhealthy[key] = True
    log(f"Key {key[:8]}... 标记为不健康: {reason}", "WARN")

def recover_key(self, key):
    """手动或定时恢复 Key（如充值后重新启用）。"""
    self._unhealthy.pop(key, None)
    self.check_quota(key)
```

## 注意事项
- `itertools.cycle` 实现无状态轮询，多线程场景需加锁或使用 `threading.Lock` 保护 `next()` 调用
- Key 在日志中只显示前 8 位（`key[:8]...`），避免泄露完整密钥
- 额度查询频率不宜过高（建议 ≥ 1 小时），避免触发平台限流
- 无限额度 Key 池为空时，服务仍可启动但应输出 WARNING 日志
- 所有 Key 不可用时应抛出明确异常（`RuntimeError`），而非静默失败
