# 预警去重：内存字典 + 时间窗口策略

## 适用场景
- 周期性任务（如 cron 每 10 分钟）中触发预警检查，同一条预警可能在多个周期重复触发
- 需要在进程内存中记录已发送的预警，避免对同一对象在短时间内重复告警
- 预警维度多样（按用户、按服务、按指标），需灵活支持多级去重 key

## 核心设计

### 1. 去重数据结构
```python
import time

# 全局预警去重表：{dedup_key: last_alert_timestamp}
_alert_history = {}

# 去重窗口（秒），同一预警在此窗口内不重复发送
_ALERT_COOLDOWN = 3600  # 1 小时
```

### 2. 构建去重 Key
```python
def _build_dedup_key(*parts):
    """拼接多维度去重 Key。

    Examples:
        _build_dedup_key("service", "LLM-API-proxy", "high_usage")
        → "service:LLM-API-proxy:high_usage"

        _build_dedup_key("user", "alice@example.com", "quota_90")
        → "user:alice@example.com:quota_90"
    """
    return ":".join(str(p) for p in parts)
```

### 3. 去重判断与记录
```python
def _should_alert(dedup_key, cooldown=_ALERT_COOLDOWN):
    """判断是否应该发送预警。

    Returns:
        bool: True 表示应发送，False 表示在冷却期内应跳过
    """
    now = time.time()
    last_sent = _alert_history.get(dedup_key, 0)
    if now - last_sent < cooldown:
        return False  # 冷却期内，跳过
    _alert_history[dedup_key] = now
    return True
```

### 4. 在预警检查中使用
```python
def check_usage_alerts(members, threshold=0.8):
    """检查用量预警，带去重。"""
    for member in members:
        usage_ratio = member.get("used", 0) / max(member.get("quota", 1), 1)
        if usage_ratio >= threshold:
            email = member.get("email", "unknown")

            # 按用户+阈值级别去重
            key = _build_dedup_key("user", email, f"usage_{int(threshold*100)}")
            if _should_alert(key):
                send_alert(f"用户 {email} 用量已达 {usage_ratio:.0%}")
            else:
                log(f"跳过重复预警: {email} (冷却中)")
```

### 5. 过期记录清理（防止内存泄漏）
```python
def _cleanup_expired_alerts(max_age=86400):
    """清理超过 24 小时的历史记录，防止内存无限增长。"""
    now = time.time()
    expired = [k for k, v in _alert_history.items() if now - v > max_age]
    for k in expired:
        del _alert_history[k]
    if expired:
        log(f"清理 {len(expired)} 条过期预警记录")

# 在每次预警检查周期开始时调用
_cleanup_expired_alerts()
```

## 适用变体
| 场景 | 去重 Key 维度 | 冷却窗口 |
|------|-------------|---------|
| 用户级用量预警 | `user:email:threshold` | 1 小时 |
| 服务级健康预警 | `service:name:status` | 30 分钟 |
| 系统资源告警 | `system:host:metric` | 15 分钟 |
| 配额到期提醒 | `quota:email:reset_date` | 24 小时 |

## 注意事项
- 去重字典是进程级内存存储，**服务重启后丢失**，适用于允许少量重复的预警场景
- 必须定期清理过期记录（`_cleanup_expired_alerts`），否则长期运行的服务会内存泄漏
- 冷却窗口（`_ALERT_COOLDOWN`）应根据预警紧急程度调整：紧急预警窗口短，普通提醒窗口长
- 去重 Key 的设计要足够精细，避免不同对象的预警被误去重（如仅用 email 不够，还需加阈值级别）
- 多线程环境下 `_alert_history` 需加锁保护（`threading.Lock`），单线程 cron 任务可省略
