# PyService _state 字典配置热重载模式

## 适用场景

PyService 平台服务的 `run(config, modules)` 函数内定义闭包处理器（如 Web 路由回调），需要在配置热重载时让闭包自动读取最新配置值，而无需重启服务。

## 核心模式

### 问题

Python 闭包捕获变量引用。`run(config, modules)` 的 `config` 参数是局部变量，热重载时 runner 调用 `on_config_reload(new_config)` 传入新配置对象，但闭包已捕获的 `config` 引用不会自动更新。

### 解决方案：模块级 `_state` 字典

```python
# 模块级共享状态
_state = {"config": {}}

def run(config, modules):
    _state["config"] = config  # 初始赋值
    # ...

def on_config_reload(new_config):
    _state["config"] = new_config  # 整体替换引用

# 闭包内通过 _state["config"] 读取，始终拿到最新值
def handle_xxx(request_info):
    cfg = _state["config"]  # ✅ 始终最新
    model = cfg.get("llm", {}).get("model")
```

### 为什么用字典而非 global

| 方案 | 闭包是否需要 nonlocal/global | 热重载是否自动生效 |
|------|---------------------------|-----------------|
| `global config` | 需要 global 声明 | ❌ 闭包已绑定旧引用 |
| `_state = {"config": {}}` | 不需要 | ✅ 字典可变，修改 key 即生效 |

**原理**：闭包捕获的是 `_state` 字典的**引用**（不可变绑定），但字典**内容**可变。`_state["config"] = new_config` 修改字典内容，闭包通过 `_state["config"]` 自然读到新值。

## 完整代码骨架

```python
import logging
logger = logging.getLogger(__name__)

_state = {"config": {}}

def run(config, modules):
    _state["config"] = config

    def handle_request(request_info):
        cfg = _state["config"]
        # ... 使用 cfg 处理请求

    # 注册路由...
    web_mod = modules.get("web-service")
    if web_mod:
        web_mod.register_handler("/api", "POST", handle_request)

    # 主循环
    while True:
        time.sleep(60)

def on_config_reload(new_config):
    _state["config"] = new_config
    logger.info("配置已重新加载")

def on_shutdown():
    logger.info("服务正在关闭")
```

## 注意事项

- `_state` 仅存放需要热重载的共享配置，不要存放请求级临时数据
- 如需多个热重载维度，可扩展键：`_state = {"config": {}, "model_cfg": {}}`
- 线程安全：字典的单项赋值在 CPython 中是原子操作（GIL 保护），足够安全
- 如果配置变更需要触发额外动作（如重建连接池），在 `on_config_reload` 中处理
