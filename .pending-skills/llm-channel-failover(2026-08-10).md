# LLM 代理多通道优先级故障转移

## 适用场景
- LLM 代理服务对接多个模型提供方（通道），如 DashScope、Azure OpenAI、自建网关等
- 需要在主通道故障时自动切换到备用通道，保障服务可用性
- 支持多优先级层级（P0 > P1 > P2），按优先级从高到低依次尝试

## 核心设计

### 1. 通道配置（config.toml）
```toml
[[channels]]
name = "dashscope-primary"
priority = 0                    # 最高优先级
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
api_key = "sk-xxx"
model = "deepseek-v3"

[[channels]]
name = "azure-backup"
priority = 1                    # 备用通道
base_url = "https://xxx.openai.azure.com/openai/deployments/deepseek-v3"
api_key = "azure-key"
model = "deepseek-v3"
extra_headers = {"api-key": "azure-key"}  # Azure 需要额外 header
```

### 2. 通道管理器
```python
class ChannelManager:
    def __init__(self, channels_config):
        # 按优先级排序：priority 越小越优先
        self._channels = sorted(channels_config, key=lambda c: c["priority"])
        self._failures = {}  # channel_name -> {"count": int, "first_fail": timestamp}
        self._recovery_interval = 300  # 5 分钟后尝试恢复

    def get_active_channel(self):
        """返回当前可用的最高优先级通道。"""
        for ch in self._channels:
            name = ch["name"]
            fail_info = self._failures.get(name)
            if fail_info is None:
                return ch
            # 超过恢复间隔，尝试重新启用
            if time.time() - fail_info["first_fail"] > self._recovery_interval:
                del self._failures[name]
                log(f"通道 {name} 恢复间隔已到，重新尝试", "INFO")
                return ch
        raise RuntimeError("所有通道均不可用")

    def report_failure(self, channel_name, error=""):
        """上报通道故障，累计失败次数。"""
        if channel_name not in self._failures:
            self._failures[channel_name] = {
                "count": 0, "first_fail": time.time()
            }
        self._failures[channel_name]["count"] += 1
        log(f"通道 {channel_name} 故障({self._failures[channel_name]['count']}次): {error}", "WARN")

    def report_success(self, channel_name):
        """上报成功，重置故障计数。"""
        self._failures.pop(channel_name, None)
```

### 3. 调用时自动降级
```python
def call_with_failover(messages, channel_mgr, **kwargs):
    """依次尝试各通道，主通道失败自动降级到备用通道。"""
    for attempt_channel in channel_mgr.iter_available():
        try:
            result = _call_single_channel(attempt_channel, messages, **kwargs)
            channel_mgr.report_success(attempt_channel["name"])
            return result
        except Exception as e:
            channel_mgr.report_failure(attempt_channel["name"], str(e))
            log(f"降级: {attempt_channel['name']} -> 下一通道", "WARN")
            continue
    raise RuntimeError("所有通道均调用失败")
```

## 注意事项
- 通道按 `priority` 升序排列（0 最高），配置时数值越小越优先
- 故障通道有自动恢复机制（默认 5 分钟），避免永久摘除
- 降级切换时必须记录日志，明确标注从哪个通道降级到哪个通道
- Azure 等特殊通道可能需要额外 HTTP header（`api-key`），通过 `extra_headers` 字段支持
- 故障计数可结合告警：连续 N 次失败时发送通知
- 所有通道均失败时抛出明确异常，由上层统一处理 503 响应
