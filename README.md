# PyService Manager

轻量级 Python 服务管理平台，通过编写自定义主函数与 TOML 配置来创建 Linux 系统服务，提供 Web 界面进行服务启停、状态监控、日志查看，并内置模块化扩展系统。

## 功能特性

- **服务生命周期管理** — 创建、启动、停止、重启服务，自动生成 systemd 单元文件（Linux）或子进程管理（macOS）
- **在线代码编辑** — 通过 Web 编辑器编写 Python 业务逻辑，或上传 `.py` 脚本文件
- **TOML 配置热加载** — 修改配置后无需重启服务，通过 SIGHUP 信号实现配置热更新
- **实时日志流** — WebSocket 驱动的实时日志查看器，支持历史日志回溯
- **模块化扩展系统** — 编写或上传 Python 模块，在服务中勾选启用，支持生命周期钩子（on_start/on_stop/on_config_reload）和工具方法注入
- **依赖管理** — 声明服务和模块的 pip 依赖（PEP 508 格式），实时检查安装状态与版本约束，一键安装缺失依赖
- **代码扫描分析** — AST 分析服务脚本和模块源码中的 import 语句，自动发现第三方依赖，与声明依赖对比，一键添加遗漏的包
- **模块复用** — 同一模块可绑定到多个服务，支持模块间共享状态
- **配置文件监控** — Watchdog 自动检测配置文件变更并触发热加载
- **响应式暗色主题界面** — 原生 HTML + JS，零构建步骤，移动端适配

## 系统架构

```
┌─────────────────────────────────────────────┐
│                  浏览器                       │
│  (SPA: 服务管理 / 模块管理 / 日志查看)         │
└──────────────┬──────────────────────────────┘
               │ HTTP / WebSocket
┌──────────────▼──────────────────────────────┐
│            FastAPI (uvicorn)                 │
│  ┌─────────┐ ┌─────────┐ ┌───────────────┐  │
│  │ 服务API  │ │ 模块API  │ │ 日志/配置 API  │  │
│  └────┬────┘ └────┬────┘ └──────┬────────┘  │
│       │           │             │            │
│  ┌────▼───────────▼─────────────▼────────┐   │
│  │          ServiceManager               │   │
│  │   SystemdBackend / ProcessBackend     │   │
│  └──────────────┬───────────────────────┘   │
│                 │                            │
│  ┌──────────────▼───────────────────────┐   │
│  │    SQLAlchemy + aiosqlite (SQLite)    │   │
│  └──────────────────────────────────────┘   │
└──────────────────────────────────────────────┘
               │ 生成 & 管理
┌──────────────▼──────────────────────────────┐
│           服务进程 (runner.py)                │
│  ┌────────┐  ┌─────────┐  ┌─────────────┐   │
│  │ 用户代码 │  │ Module  │  │ Config      │   │
│  │ run()   │  │ Proxy   │  │ Hot-Reload  │   │
│  └────────┘  └─────────┘  └─────────────┘   │
└──────────────────────────────────────────────┘
```

## 快速开始

### 环境要求

- Python >= 3.11
- Linux（生产环境，使用 systemd）或 macOS（开发环境，使用 ProcessBackend）

### 安装

```bash
git clone https://github.com/paulyao/PyServices.git
cd PyServices

python -m venv .venv
source .venv/bin/activate

pip install -e .
```

### 启动

```bash
# 通过 CLI 启动
pyservice

# 或直接运行
python -m app.main
```

服务默认监听 `http://0.0.0.0:8900`，在浏览器中打开即可访问管理界面。

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PYSERVICE_HOST` | `0.0.0.0` | 监听地址 |
| `PYSERVICE_PORT` | `8900` | 监听端口 |
| `PYSERVICE_PYTHON` | `sys.executable` | 服务进程使用的 Python 解释器路径 |

## 项目结构

```
PyServices/
├── app/
│   ├── main.py              # FastAPI 入口与 CLI
│   ├── config.py            # 平台配置
│   ├── database.py          # 数据库引擎与会话
│   ├── api/
│   │   ├── services.py      # 服务 CRUD 与控制 API
│   │   ├── modules.py       # 模块 CRUD 与绑定 API
│   │   ├── config.py        # 配置读写 API
│   │   └── logs.py          # 日志历史与 WebSocket
│   ├── core/
│   │   ├── service_manager.py   # 服务生命周期管理（双后端）
│   │   ├── module_manager.py    # 模块管理与绑定
│   │   ├── module_loader.py     # 模块验证与模板
│   │   ├── runner_template.py   # Jinja2 runner/unit 生成
│   │   ├── config_watcher.py    # Watchdog 配置监控
│   │   └── log_streamer.py      # 日志流管理
│   ├── models/
│   │   ├── service.py       # 服务 ORM 模型
│   │   ├── module.py        # 模块 ORM 模型
│   │   └── service_module.py # 服务-模块关联模型
│   ├── schemas/
│   │   ├── service.py       # 服务 Pydantic 模型
│   │   └── module.py        # 模块 Pydantic 模型
│   └── utils/
│       ├── dependency.py    # 依赖检查与安装
│       ├── import_scanner.py # 代码扫描（AST 分析）
│       ├── validation.py    # 输入验证
│       └── system.py        # 系统工具函数
├── static/
│   ├── index.html           # SPA 入口
│   ├── css/style.css        # 暗色主题样式
│   └── js/
│       ├── api.js           # API 封装
│       ├── app.js           # 路由与通用工具
│       ├── services.js      # 服务管理页面
│       └── modules.js       # 模块管理页面
├── templates/
│   ├── runner.py.j2         # 服务运行器模板
│   └── service.unit.j2      # systemd 单元文件模板
├── data/                    # 运行时数据（自动生成）
│   ├── platform.db          # SQLite 数据库
│   ├── services/            # 服务脚本与配置
│   └── modules/             # 模块脚本与配置
└── pyproject.toml
```

## 使用指南

### 创建服务

1. 在 Web 界面点击「新建服务」
2. 填写服务名称、显示名称和描述
3. 在在线编辑器中编写 Python 代码，或选择上传文件
4. 点击「创建」

服务代码模板：

```python
import time

def run(config, modules):
    """主入口函数。
    config: 来自 config.toml 的字典
    modules: 模块命名空间字典，例如 modules["module-name"].method()
    """
    interval = config.get("interval", {}).get("seconds", 5)
    message = config.get("message", {}).get("text", "Hello!")

    while True:
        print(f"[service] {message}")
        time.sleep(interval)

def on_config_reload(new_config):
    """配置热加载时调用"""
    print("配置已重新加载")

def on_shutdown():
    """服务关闭时调用"""
    print("服务正在关闭")
```

### 配置热加载

服务的配置文件为 `config.toml`，修改后保存即可触发热加载：

```toml
[message]
text = "Hello from config!"

[interval]
seconds = 3
```

在 Web 界面的「配置」标签页中编辑并点击「保存并热加载」，运行中的服务会立即收到 SIGHUP 信号并重新读取配置。

### 创建模块

1. 在「模块管理」页面点击「新建模块」
2. 编写模块代码，支持生命周期钩子和工具方法

模块代码模板：

```python
from pathlib import Path


class Module:
    """模块元数据"""
    name = "my-module"
    version = "1.0.0"
    description = "模块描述"

    # 生命周期钩子（可选）
    def on_start(self, ctx):
        """服务启动时调用"""
        ctx.logger.info(f"模块 {self.name} 已启动")

    def on_stop(self, ctx):
        """服务停止时调用"""
        ctx.logger.info(f"模块 {self.name} 已停止")

    def on_config_reload(self, ctx):
        """配置热加载时调用"""
        pass

    # 工具方法（在用户代码中通过 modules["module-name"] 访问）
    def my_utility(self, *args, **kwargs):
        """供用户代码调用的工具函数"""
        pass
```

### 绑定模块到服务

1. 进入服务详情页的「模块」标签页
2. 勾选需要启用的模块
3. 点击「保存模块」并重启服务

模块启用后，在服务代码中可通过 `modules` 参数访问模块方法：

```python
def run(config, modules):
    result = modules["my-module"].my_utility()
```

### 依赖管理

服务和模块均支持声明 pip 依赖（PEP 508 格式），平台自动检查安装状态和版本约束。

**声明依赖**：在服务详情页的「依赖」标签页中，每行填写一个依赖：

```
requests>=2.28
pymysql>=1.1
certifi
```

**检查依赖**：点击「检查所有依赖」，平台会：
- 检查声明的依赖是否已安装且版本满足
- AST 分析服务 main.py 和启用模块的 module.py 中的 import 语句
- 对比声明与代码扫描结果，标记遗漏或过时的依赖

**一键安装**：点击「一键安装缺失依赖」自动安装未满足的包。

## API 概览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/health` | 健康检查 |
| GET | `/api/v1/services` | 列出所有服务 |
| POST | `/api/v1/services` | 创建服务 |
| GET | `/api/v1/services/{name}` | 获取服务详情 |
| DELETE | `/api/v1/services/{name}` | 删除服务 |
| POST | `/api/v1/services/{name}/start` | 启动服务 |
| POST | `/api/v1/services/{name}/stop` | 停止服务 |
| POST | `/api/v1/services/{name}/restart` | 重启服务 |
| GET | `/api/v1/services/{name}/code` | 获取服务代码 |
| PUT | `/api/v1/services/{name}/code` | 更新服务代码 |
| GET | `/api/v1/services/{name}/config` | 获取服务配置 |
| PUT | `/api/v1/services/{name}/config` | 更新服务配置（触发热加载） |
| GET | `/api/v1/services/{name}/logs` | 获取历史日志 |
| WS | `/api/v1/services/{name}/logs/ws` | 实时日志流 |
| GET | `/api/v1/services/{name}/deps` | 检查服务依赖状态（?scan=true 启用代码扫描） |
| POST | `/api/v1/services/{name}/deps/install` | 安装服务缺失依赖 |
| PUT | `/api/v1/services/{name}/requirements` | 更新服务依赖声明 |
| GET | `/api/v1/modules` | 列出所有模块 |
| POST | `/api/v1/modules` | 创建模块 |
| POST | `/api/v1/modules/validate` | 验证模块代码 |
| GET | `/api/v1/modules/{name}` | 获取模块详情 |
| PUT | `/api/v1/modules/{name}` | 更新模块信息 |
| DELETE | `/api/v1/modules/{name}` | 删除模块 |
| GET | `/api/v1/modules/{name}/code` | 获取模块代码 |
| PUT | `/api/v1/modules/{name}/code` | 更新模块代码 |
| GET | `/api/v1/modules/service/{name}` | 获取服务的模块绑定 |
| PUT | `/api/v1/modules/service/{name}` | 更新服务的模块绑定 |
| GET | `/api/v1/modules/{name}/deps` | 检查模块依赖状态 |
| POST | `/api/v1/modules/{name}/deps/install` | 安装模块缺失依赖 |
| PUT | `/api/v1/modules/{name}/requirements` | 更新模块依赖声明 |

## 技术栈

| 组件 | 技术 |
|------|------|
| Web 框架 | FastAPI |
| ASGI 服务器 | Uvicorn |
| ORM | SQLAlchemy 2.0 (async) |
| 数据库 | SQLite (aiosqlite) |
| 模板引擎 | Jinja2 |
| 文件监控 | Watchdog |
| 配置格式 | TOML |
| 依赖解析 | packaging (PEP 508) |
| 前端 | 原生 HTML + CSS + JavaScript |
| 服务管理 | systemd (Linux) / subprocess (macOS) |

## License

MIT
