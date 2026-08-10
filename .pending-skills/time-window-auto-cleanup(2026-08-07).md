# 时间窗口条件自动清理

## 适用场景
- 在周期性任务中，根据某个时间戳字段（如"下次重置时间"）判断是否自动执行清理/删除操作
- 需同时满足时间窗口条件和业务条件（如用量为零）

## 核心步骤

### 1. 解析 ISO 时间字符串（含 Z 后缀处理）
```python
from datetime import datetime, timezone

next_reset_str = quota.get("nextResetAt", "")  # e.g. "2026-08-08T12:00:00Z"
if not next_reset_str:
    return  # 无时间戳，跳过

# 将 "Z" 替换为 "+00:00" 以兼容 Python 3.10 以下版本
reset_dt = datetime.fromisoformat(next_reset_str.replace("Z", "+00:00"))
now_utc  = datetime.now(timezone.utc)
```

### 2. 计算时间差并判断窗口
```python
hours_left = (reset_dt - now_utc).total_seconds() / 3600

# 仅当距重置不足 24 小时且业务条件满足时执行清理
if 0 <= hours_left < 24 and total_used == 0:
    # 执行删除/清理操作
    result = http_client.delete(url, headers=headers)
    if result.get("success"):
        log(f"自动删除: {email} (距重置 {hours_left:.1f}h, 已用=0)")
    else:
        log(f"自动删除失败: {email} - {result.get('error')}", "WARN")
```

### 3. 异常隔离（不阻断主循环）
```python
try:
    # ... 时间解析与清理逻辑 ...
except Exception as e:
    log(f"自动删除检查异常: {email} - {e}", "WARN")
```

## 适用变体
| 场景 | 时间字段 | 条件组合 |
|------|---------|---------|
| 配额到期自动删除成员 | `nextResetAt` | 距重置 < 24h 且已用=0 |
| 临时账号到期清理 | `expiresAt` | 已过期且未登录 |
| 缓存/会话 TTL 清理 | `lastAccessAt` | 距最后访问 > N 天 |

## 注意事项
- 时间字符串的 `Z` 后缀必须替换为 `+00:00`，否则 `fromisoformat` 在旧版 Python 会报错
- 清理操作必须包裹 try-except，单条失败不应影响后续数据处理
- 失败和成功均需记录日志，方便排查问题
- 使用 `0 <= hours_left` 避免处理已过期的记录（负值）

