---
name: pyservice-dev
description: 通过 REST API 在 PyService 平台（http://10.0.29.37:8900）上编写自定义模块与自定义服务。适用于只能访问平台 HTTP 接口、看不到后端源码的 Agent。涵盖模块/服务编写规范、ctx 对象、生命周期钩子、内置模块方法速查、web-service 注册 API、完整 REST 端点参考。
---

# PyService 自定义模块与自定义服务开发指南（API-only 版）

你只能通过 HTTP 访问 PyService 平台，没有文件系统访问权。一切操作走 REST API：

- **管理 API**：`BASE = http://10.0.29.37:8900`，前缀 `/api/v1`
- **Web UI**（人类用，Agent 可忽略）：同地址首页
- **web 页面出口**：`http://10.0.29.37:8910/{service_name}/...`（服务注册的 Web 页面/API，见下文 web-service 节）

示例统一用 `curl $BASE` 表示。API 无鉴权，直接调用即可。

## 核心概念

- **模块（Module）**：可复用功能单元，代码是一个 `module.py`（+ 可选 `config.toml`），通过 API 提交。5 个内置模块（http-client、sqlite-helper、mysql-helper、log-enhancer、web-service）可直接绑定使用，方法速查见下文。
- **服务（Service）**：你的业务代码 `main.py` + 配置 `config.toml`，平台为其生成 runner 并作为系统服务托管（Linux systemd / macOS 进程，Agent 无需关心）。
- **绑定**：服务可绑定多个模块；运行时模块实例注入到你的 `run(config, modules)` 里。

## 标准工作流

```bash
# 1. 查看现有服务与模块（也用于确认命名不冲突）
curl $BASE/api/v1/services
curl $BASE/api/v1/modules

# 2. 创建模块（可选——内置模块已存在，只有新功能才需要自建）
curl -X POST $BASE/api/v1/modules -H 'Content-Type: application/json' -d '{
  "name": "my-module",            # 字母开头, [a-zA-Z0-9_-], 1-64字符
  "display_name": "我的模块",
  "description": "功能描述",
  "code": "<module.py 全文>",
  "config_toml": "<config.toml 全文，可选>",
  "requirements": ["pymysql>=1.0"]   # PEP 508 依赖，可选
}'

# 3. 创建服务
curl -X POST $BASE/api/v1/services -H 'Content-Type: application/json' -d '{
  "name": "my-service",
  "display_name": "我的服务",
  "description": "...",
  "code": "<main.py 全文>",          # 省略则用默认模板
  "auto_restart": true
}'

# 4. 绑定模块（返回 restart_required，绑定变更必须重启服务生效）
curl $BASE/api/v1/modules/service/my-service   # 先查这个拿 module_id 列表
curl -X PUT $BASE/api/v1/modules/service/my-service -H 'Content-Type: application/json' -d '{
  "modules": [{"module_id": 2, "enabled": true, "load_order": 10}]
}'

# 5. 安装依赖（聚合服务自身 + 已启用模块的依赖）
curl -X POST $BASE/api/v1/services/my-service/deps/install

# 6. 启动 / 重启 / 停止
curl -X POST $BASE/api/v1/services/my-service/start
curl -X POST $BASE/api/v1/services/my-service/restart
curl -X POST $BASE/api/v1/services/my-service/stop

# 7. 查看日志排错（tail）
curl "$BASE/api/v1/services/my-service/logs?lines=100"
```

学习现成写法：`curl $BASE/api/v1/services/{name}/code` 可读任意服务的 main.py；`curl $BASE/api/v1/modules/{name}/code` 可读任意模块源码（含内置模块）——这是看不到源码时最重要的参考资料来源。

## REST API 参考

**服务**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/services` | 列表 |
| POST | `/api/v1/services` | 创建 |
| GET/PUT/DELETE | `/api/v1/services/{name}` | 详情 / 改元数据 / 删除 |
| POST | `/{name}/start` `/{name}/stop` `/{name}/restart` `/{name}/enable` `/{name}/disable` | 生命周期 |
| GET | `/{name}/status` | 运行状态 |
| GET/PUT | `/{name}/code` | 读/写 main.py |
| GET/PUT | `/{name}/config` | 读/写 config.toml（写入自动触发热加载） |
| GET | `/{name}/logs?lines=N` | 历史日志 |
| WS | `/{name}/logs/ws` | 实时日志流 |
| GET | `/{name}/deps?scan=true` | 依赖状态；`scan=true` 附带 AST import 扫描，对比声明依赖与实际 import |
| POST | `/{name}/deps/install` | 安装缺失依赖 |
| PUT | `/{name}/requirements` | 更新服务依赖声明 |

**模块**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/api/v1/modules` | 列表 / 创建 |
| GET/PUT/DELETE | `/api/v1/modules/{name}` | 详情 / 改元数据 / 删除（内置模块不可删，403） |
| GET/PUT | `/{name}/code` | 读/写 module.py + config.toml（改后需重启绑定服务） |
| POST | `/api/v1/modules/validate` | 校验模块代码 `{code}`，返回 `{valid, errors, module_info}` |
| GET/PUT | `/api/v1/modules/service/{service_name}` | 查询/设置服务绑定 |
| GET | `/{name}/services` | 哪些服务绑定了它 |
| GET/POST | `/{name}/deps`、`/{name}/deps/install` | 依赖检查/安装 |
| PUT | `/{name}/requirements` | 更新依赖声明 |

**备份**：`GET /api/v1/backup/items`、`POST /api/v1/backup/create|preview|restore`。

## 编写模块（module.py）

约定式结构：文件必须叫 `module.py`，类名必须是 `Module`，无参构造，元数据写类属性：

```python
"""Module: my-module"""


class Module:
    name = "my-module"          # 必须与模块名一致
    version = "1.0.0"
    description = "模块用途描述"

    def on_start(self, ctx):
        ctx.logger.info(f"Module {self.name} starting")
        # 初始化连接、启动后台线程等

    def on_stop(self, ctx):
        # 释放资源、停线程
        pass

    def on_config_reload(self, ctx):
        pass

    def on_error(self, ctx, error):
        pass

    def my_utility(self, arg):
        # 所有公开方法（非 _ / on_ 前缀）供服务代码调用
        # 统一返回结构，参考内置模块
        return {"success": True, "data": {"result": arg}, "error": None}
```

- 生命周期钩子全部可选。启动顺序：按 load_order 升序逐个加载模块并立即调 `on_start` → 加载服务 main.py → 调 `run()`。停止时逆序调 `on_stop`。
- 单个模块加载/on_start 失败只记日志、服务照常启动；你的 main.py 加载失败则服务直接退出并自动重启。
- 涉及连接/客户端的模块，用线程锁保护懒初始化（并发调用来自你的服务线程）。

**模块 config.toml**：只放自定义配置节，代码里用 `ctx.module_config["节名"]["键"]` 读取，务必给默认值兜底：

```python
timeout = ctx.module_config.get("defaults", {}).get("timeout", 30)
```

**ctx 对象**（runner 注入，仅 6 个属性）：

| 属性 | 说明 |
|---|---|
| `ctx.service_name` | 宿主服务名 |
| `ctx.config` | **服务**的 config.toml（只读，写入抛异常） |
| `ctx.module_config` | **本模块**的 config.toml（只读）。模块读自身配置必须用它 |
| `ctx.data_dir` | 服务数据目录（Path）。模块数据放专属子目录：`ctx.data_dir / ".my-module/"` |
| `ctx.logger` | 日志器，输出到服务日志 |
| `ctx.shared_state` | 全服务共享 dict，跨模块传数据（没有 ctx.get_module） |

## 编写服务（main.py）

```python
"""Service: my-service"""
import time


def run(config, modules):
    interval = config.get("interval", {}).get("seconds", 5)
    http = modules.get("http-client")   # 未绑定/未启用返回 None，务必判空
    while True:
        if http:
            result = http.get("https://example.com/api")
            if result["success"]:
                print(result["json"])
        time.sleep(interval)


def on_config_reload(new_config):
    # 服务 config.toml 变更（热加载）时回调
    pass


def on_shutdown():
    pass
```

- 入口签名：`run(config, modules)`（或 `main(...)`）。`config` 是服务 config.toml 的 dict，`modules` 是 `{模块名: 代理对象}`。
- `run()` 抛异常 → 服务退出并自动重启（auto_restart=true 时）。
- 模块方法经代理调用，只暴露公开可调用方法。

## 内置模块方法速查

所有方法返回 dict，通用字段 `success`（bool）/`error`（str|None），数据在 `data` 或平铺字段（http-client）。**完整源码可随时 `curl $BASE/api/v1/modules/{name}/code` 查看。**

**http-client** — HTTP 请求（自动重试，配置在模块 config.toml `[retry]`）

```python
http.get(url, *, params=None, headers=None, timeout=30)
http.post(url, *, data=None, json=None, headers=None, timeout=30)   # json= 自动设 Content-Type
http.put(url, *, data=None, json=None, headers=None, timeout=30)
http.delete(url, *, headers=None, timeout=30)
http.request(method, url, *, params=None, data=None, json=None, headers=None, timeout=30)
# 返回: {"success", "status_code", "headers", "body"(str), "json"(dict/list), "error"}
```

**sqlite-helper** — SQLite 操作（标准库，占位符用 `?` 防注入）

```python
db = "/path/to/db.sqlite"   # 路径建议放 ctx.data_dir 下
sqlite.query(db, "SELECT * FROM t WHERE x=?", (v,))       # → data.rows = [dict,...]
sqlite.query_one(db, sql, params)                          # → data.row = dict|None
sqlite.execute(db, sql, params, commit=False)              # → data.rowcount
sqlite.batch_insert(db, "INSERT INTO t VALUES(?,?)", rows) # → data.rowcount
```

**mysql-helper** — MySQL（pymysql + 连接池）

```python
mysql.execute(*, host, port=3306, user, password, database, sql, params=None, commit=False, as_dict=True)
mysql.execute_many(*, host, port=3306, user, password, database, sql, params_list=None, commit=True)
mysql.test_connection(*, host, port=3306, user, password, database)
```

**log-enhancer** — `log(message, level="INFO")`，写入增强日志。

**web-service** — 给服务挂 Web 页面/API，见下节。

## web-service 集成（给服务加 Web 页面/API）

绑定 web-service 模块后，`web = modules.get("web-service")`：

| 方法 | 用途 |
|---|---|
| `register_page(path, html, *, auth=None, rate_limit=10)` | 静态 HTML 页面（GET） |
| `register_api(path, callback, *, auth=None, rate_limit=10)` | 数据 API；callback **无参**返回 dict，后台每 5 秒刷新快照 |
| `register_handler(path, method, callback, *, auth=None, rate_limit=10)` | 实时处理器；GET=快照，POST/PUT/DELETE/PATCH=实时回调 |
| `unregister(path)` / `get_port()` | 注销路由 / 取端口 |

```python
web.register_page("/status", "<html>...</html>")
web.register_api("/api/stats", lambda: {"ok": True, "count": get_count()})

def handle_post(request_info):
    # request_info = {"method", "path", "headers", "body", "query"}
    return {"status_code": 200, "content_type": "application/json", "body": '{"ok": true}'}
web.register_handler("/api/update", "POST", handle_post)

web.register_api("/api/private", cb,
                 auth={"header_name": "X-API-Key", "header_value": "secret"},
                 rate_limit=5)   # QPS，0=不限流，默认 10；鉴权失败 401，超限 429
```

- **URL 前缀自动注入**：注册 `/x` 实际访问 `http://10.0.29.37:8910/{service_name}/x`。
- 8910 守护进程为多服务共享单例；POST 类请求代理到服务内回调服务器，超时 300 秒。
- 回调请求体只做 utf-8 解码——图像/文件必须用 base64 JSON 收发；callback 里不要做长阻塞任务。

## 热重载边界（易踩坑）

- 改**服务** config.toml（API PUT）→ 自动热加载：模块的 `ctx.config` 更新 + 各模块 `on_config_reload` + 服务 `on_config_reload(new_config)`。
- 改**模块** config.toml 或 module.py → **不热加载**，必须 restart 绑定的服务。
- 改服务 main.py（PUT code）→ 需 restart。
- 模块绑定变更（PUT /modules/service/{name}）→ 需 restart（响应里 restart_required=true）。
- 依赖检查只告警不阻断启动：启动前先 `deps/install`，启动后看日志确认无 ImportError。

## 调试排错

1. `curl $BASE/api/v1/services/{name}/status` — 是否 running
2. `curl "$BASE/api/v1/services/{name}/logs?lines=200"` — 找 traceback
3. `curl "$BASE/api/v1/services/{name}/deps?scan=true"` — 声明依赖与实际 import 的差集
4. `POST /api/v1/modules/validate` — 提交模块代码前先校验
5. 模块内日志用 `ctx.logger`（带模块名前缀），服务代码直接 `print`，都进服务日志
