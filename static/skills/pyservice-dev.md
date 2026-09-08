---
name: pyservice-dev
description: 在 PyService 平台上编写自定义模块（module.py + config.toml）与自定义服务（main.py）。当用户要在 PyService 上开发新模块、新服务、扩展现有模块，或询问 Module 类、ctx 对象、生命周期钩子、模块绑定、web-service 注册 API 时使用。
---

# PyService 自定义模块与自定义服务开发指南

平台 = FastAPI 管理器（`app/main.py`），API 前缀 `/api/v1`，Web UI 端口 8900。**没有创建模块/服务的 CLI**，一切通过 Web UI 或 REST API 完成。

## 核心概念

- **模块（Module）**：可复用的功能单元，位于 `data/modules/{name}/`（内置模块源码在 `builtin_modules/{name}/`，启动时自动复制注册）。结构：`module.py`（必需）+ `config.toml`（可选）+ 附属文件。
- **服务（Service）**：用户业务代码，位于 `data/services/{name}/`，含 `main.py`（用户代码）+ `config.toml` + `runner.py`（平台自动生成，勿手改）+ `.modules.json`（模块绑定注册表）。
- **绑定**：服务通过 `ServiceModule` 关联模块（enabled + load_order），runner 启动时按 load_order 加载模块并注入到用户代码的 `modules` 字典。
- **运行方式**：Linux 走 SystemdBackend（unit 文件），macOS 走 ProcessBackend（subprocess + .pid 文件）。用户代码无需关心后端差异。

## 编写自定义模块

### module.py 规范（约定式，非配置式）

- 文件名**必须**是 `module.py`，类名**必须**是 `Module`，无参构造。
- 元数据（name/version/description）写在**类属性**里，由 `app/utils/validation.py` AST 提取入 DB。config.toml 里**没有**元数据/`[dependencies]`/`[service]` 段。
- 生命周期钩子全部可选：`on_start(ctx)`、`on_stop(ctx)`、`on_config_reload(ctx)`、`on_error(ctx, error)`。
- 公开方法（非 `_`/`on_` 前缀）通过 `modules["模块名"].方法()` 供用户代码调用，统一返回 `{"success": bool, "data": ..., "error": str|None}`。

标准模板：

```python
"""Module: my-module"""


class Module:
    name = "my-module"          # 必须与模块目录名一致
    version = "1.0.0"
    description = "模块用途描述"

    def on_start(self, ctx):
        ctx.logger.info(f"Module {self.name} starting")
        # 初始化连接、启动后台线程等

    def on_stop(self, ctx):
        # 释放资源、停线程；web-service 模块的 on_stop 会注销全部路由
        pass

    def on_config_reload(self, ctx):
        pass

    def my_utility(self, arg):
        return {"success": True, "data": ..., "error": None}
```

连接型模块（如 mysql-helper、idaas-eiam）用线程锁保护客户端懒初始化，参考 `builtin_modules/mysql-helper/module.py`。

### 模块 config.toml

只放自定义配置节，按 `[节名]` 组织，模块代码用 `ctx.module_config["节名"]["键"]` 读取：

```toml
[defaults]
timeout = 30
user_agent = "my-agent/1.0"

[retry]
enabled = true
max_retries = 3
```

注意读取处要给默认值兜底：`ctx.module_config.get("retry", {}).get("max_retries", 3)`（config.toml 缺失时 `ctx.module_config` 为空 dict）。

### ctx 对象（仅 6 个属性，全部由 runner 注入）

| 属性 | 说明 |
|---|---|
| `ctx.service_name` | 宿主服务名 |
| `ctx.config` | **服务** config.toml（只读 `_ReadOnlyDict`，写入抛 RuntimeError） |
| `ctx.module_config` | **本模块** config.toml（只读）。模块读自身配置必须用它，不要用 `ctx.config` |
| `ctx.data_dir` | Path，指向**服务目录**（非模块专属）。模块数据应放子目录，如 `ctx.data_dir / ".my-module/"` |
| `ctx.logger` | 日志器 `{service}.module.{module}` |
| `ctx.shared_state` | 全服务共享 dict，跨模块传数据用这个（没有 `ctx.get_module`） |

### 创建与更新模块（REST API）

```
POST   /api/v1/modules                 # 创建：{name, code(module.py 内容), config(可选 TOML 字符串), requirements(可选)}
PUT    /api/v1/modules/{name}/code     # 更新 module.py，需重启绑定服务生效
PUT    /api/v1/modules/{name}/config   # 更新 config.toml，需重启生效（见下方热重载限制）
PUT    /api/v1/modules/{name}/requirements  # 更新 PEP 508 依赖列表（存 DB，不写 config.toml）
POST   /api/v1/modules/{name}/deps/install  # 安装依赖（uv pip install 优先，回退 pip）
```

模块名格式：字母开头、`[a-zA-Z0-9_-]`、1-64 字符。依赖是 PEP 508 字符串（如 `pymysql>=1.0`）。

## 编写自定义服务（main.py）

入口签名二选一：`run(config, modules)` 或 `main(...)`。`config` 是服务 config.toml 的 dict（只读快照），`modules` 是 `{小写模块名: ModuleProxy}`。

```python
"""Service: my-service"""
import time


def run(config, modules):
    interval = config.get("interval", {}).get("seconds", 5)
    http = modules.get("http-client")   # 未绑定/未启用返回 None，务必判空
    web = modules.get("web-service")

    while True:
        if http:
            result = http.request("GET", "https://example.com/api")
        time.sleep(interval)


def on_config_reload(new_config):
    # 服务 config.toml 变更（SIGHUP 热加载）时回调，new_config 是新 dict
    pass


def on_shutdown():
    pass
```

要点：
- `run()` 抛异常 → runner 逐模块调 `on_error` 后进程 exit(1)，Restart=on-failure 会拉起。
- ModuleProxy 只暴露非 `_`/`on_` 前缀的可调用方法。
- 用户代码加载失败直接 exit(1)；单个模块加载/on_start 失败只记日志、服务继续。
- 服务配置热加载只刷新 `ctx.config`（各模块）；用户侧通过 `on_config_reload(new_config)` 拿新配置。

## web-service 模块集成（给服务加 Web 页面/API）

先绑定 web-service 模块，然后 `web = modules.get("web-service")` 调用其方法（**注意：register_* 是模块方法，不是 ctx 方法**）：

| 方法 | 用途 |
|---|---|
| `register_page(path, html, *, auth=None, rate_limit=10)` | 注册静态 HTML 页面（GET） |
| `register_api(path, callback, *, auth=None, rate_limit=10)` | 注册数据 API；callback **无参**返回 dict，后台线程每 5 秒刷新快照 |
| `register_handler(path, method, callback, *, auth=None, rate_limit=10)` | 注册实时处理器；callback(request_info) → response_dict。GET=快照模式，POST/PUT/DELETE/PATCH=实时回调 |
| `unregister(path)`、`get_port()` | 注销路由、取端口 |

```python
web.register_page("/status", "<html>...</html>")

web.register_api("/api/stats", lambda: {"ok": True, "count": get_count()})

def handle_post(request_info):
    # request_info = {"method", "path", "headers", "body", "query"}
    return {"status_code": 200, "content_type": "application/json", "body": '{"ok": true}'}
web.register_handler("/api/update", "POST", handle_post)

# 鉴权 + 限流（QPS，0=不限流，默认 10）
web.register_api("/api/private", callback,
                 auth={"header_name": "X-API-Key", "header_value": "secret"},
                 rate_limit=5)
```

- **URL 前缀自动注入**：注册 `/x` 实际路径是 `/{service_name}/x`，访问 `http://<host>:8910/{service_name}/x`。
- web_daemon（端口 8910）是多服务共享单例；POST 类请求代理到服务内随机端口回调服务器，超时 300 秒。
- 回调请求体只做 utf-8 有损解码，图像/文件端点必须用 base64 JSON 收发。
- callback 抛错或阻塞会拖慢代理层，长任务放后台线程。

## 服务创建与模块绑定全流程

```
POST /api/v1/services                        # 创建服务（生成 main.py/config.toml/runner.py/unit）
PUT  /api/v1/services/{name}/code            # 写入业务代码
PUT  /api/v1/services/{name}/config          # 写入配置（自动 SIGHUP 热加载）
PUT  /api/v1/modules/service/{name}          # 绑定模块 {module_name, enabled, load_order} → 重写 .modules.json
POST /api/v1/services/{name}/deps/install    # 聚合安装服务+启用模块的依赖
POST /api/v1/services/{name}/start           # 启动（模块绑定变更后必须重启生效）
```

模块间有依赖顺序时用 `load_order` 控制（runner 按 load_order 升序加载并立即调各模块 on_start，之后才加载用户 main.py；停止时逆序调 on_stop）。

## 热重载边界（易踩坑）

- watchdog 只监控**服务**的 config.toml（`data/services/*/config.toml`），变更后 500ms 去抖发 SIGHUP。
- SIGHUP 只刷新 `ctx.config` 和用户 `on_config_reload`；**模块自身 config.toml 不被监控且不会刷新** `ctx.module_config`，改模块配置必须重启服务。
- `runner.py`、`.modules.json` 由平台管理，不要手动编辑；代码更新走 API。

## 验证与调试

- 模块代码校验：`app/utils/validation.py` 的 `validate_module_code()`（检查语法、Module 类、提取方法列表）；也可调 `POST /api/v1/modules/{name}/validate`。
- 日志：`data/services/{name}/runner.log`，或 Web UI 实时日志流（WebSocket）；模块日志用 `ctx.logger`，服务代码可直接 print。
- 依赖核对：`POST /api/v1/services/{name}/scan` 做 AST import 扫描与声明依赖对比，避免"运行时才发现缺包"。
- 平台自己的依赖检查仅告警不阻断启动，发布前务必手动验证依赖已装。
- 部署到其他机器时模块路径以相对路径存 DB，跨目录部署用平台的 `repair_paths` 自动修复，不要手搬 `data/` 目录。

## 参考实现

| 需求 | 参考模块 |
|---|---|
| 纯标准库工具模块 | `builtin_modules/sqlite-helper/`、`builtin_modules/log-enhancer/` |
| 带配置节 + 重试策略 | `builtin_modules/http-client/` |
| 连接池/线程锁模式 | `builtin_modules/mysql-helper/` |
| 附属文件 + 守护进程 + Web 注册 API | `builtin_modules/web-service/` |
| 真实服务调用模块 | `data/services/heartbeat/main.py` |
