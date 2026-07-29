"""Heartbeat 服务 — 内置模块调用示例

分块演示平台 6 个内置模块的标准调用方式，每个模块对应一个独立的 demo 函数，
启动时依次执行一遍，之后进入心跳主循环。

示例模块：
1. log-enhancer   — 增强日志输出（log 方法 + 日志保留清理）
2. http-client    — 通用 HTTP 请求（GET/POST，结构化返回 + 自动重试）
3. sqlite-helper  — SQLite 读写（建表/插入/批量插入/查询）
4. mysql-helper   — MySQL 读写（调用方提供连接参数，连接池复用）
5. idaas-eiam     — 阿里云 IDaaS EIAM API（需配置 AK/SK 才能真正调用）
6. web-service    — 统一 HTTP 服务器（注册页面/API，URL 自动加服务名前缀）

所有模块方法均返回统一结构：{"success": bool, "data": ..., "error": str|None}，
未绑定/未启用的模块 modules.get() 返回 None，示例会跳过并记录日志。
"""
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# 服务数据目录（sqlite 示例数据库存放于此）
_SERVICE_DIR = Path(__file__).resolve().parent


# ═══════════════════════════════════════════════════════════════
# 1. log-enhancer — 增强日志模块
#    公开方法：log(message, level="INFO")
#    附加能力：后台自动清理 runner.log 中超过保留天数的旧日志
# ═══════════════════════════════════════════════════════════════

def demo_log_enhancer(modules):
    log_mod = modules.get("log-enhancer")
    if not log_mod:
        logger.warning("[示例1 log-enhancer] 模块未启用，跳过")
        return

    # 按级别输出格式化日志（时间戳 + 级别自动拼接）
    log_mod.log("[示例1 log-enhancer] 普通信息日志")
    log_mod.log("[示例1 log-enhancer] 警告日志", level="WARNING")
    log_mod.log("[示例1 log-enhancer] 错误日志", level="ERROR")


# ═══════════════════════════════════════════════════════════════
# 2. http-client — 通用 HTTP 请求模块
#    公开方法：get / post / put / delete / request
#    特点：结构化返回（status_code/json/body），5xx 与网络错误自动指数退避重试
# ═══════════════════════════════════════════════════════════════

def demo_http_client(modules, log):
    http_mod = modules.get("http-client")
    if not http_mod:
        log("[示例2 http-client] 模块未启用，跳过", "WARNING")
        return

    # GET 请求：查询平台自身的服务列表 API（headers/params 可选）
    result = http_mod.get(
        "http://localhost:8900/api/v1/services",
        headers={"Accept": "application/json"},
        timeout=5,
    )
    if result["success"]:
        names = [s["name"] for s in result["json"]]
        log(f"[示例2 http-client] GET 成功 status={result['status_code']} "
            f"retry={result['retry_count']} 服务列表={names}")
    else:
        log(f"[示例2 http-client] GET 失败: {result['error']}", "WARNING")

    # POST 请求：json 参数自动序列化并设置 Content-Type
    result = http_mod.post(
        "http://localhost:8900/api/v1/modules/validate",
        json={"code": "class Module:\n    name = 'demo'"},
        timeout=5,
    )
    log(f"[示例2 http-client] POST 校验模块代码 status={result['status_code']} "
        f"body={result['body'][:80] if result['body'] else result['error']}")


# ═══════════════════════════════════════════════════════════════
# 3. sqlite-helper — SQLite 读写模块
#    公开方法：execute / query / query_one / batch_insert
#    特点：每次调用独立开关连接，? 占位符防注入，行以 dict 返回
# ═══════════════════════════════════════════════════════════════

def demo_sqlite_helper(modules, log):
    sqlite_mod = modules.get("sqlite-helper")
    if not sqlite_mod:
        log("[示例3 sqlite-helper] 模块未启用，跳过", "WARNING")
        return

    db_path = str(_SERVICE_DIR / "demo.sqlite")

    # 建表（DDL 需 commit=True）
    sqlite_mod.execute(
        db_path=db_path,
        sql="CREATE TABLE IF NOT EXISTS heartbeat_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, note TEXT)",
        commit=True,
    )

    # 单条插入（? 占位符传参）
    sqlite_mod.execute(
        db_path=db_path,
        sql="INSERT INTO heartbeat_log (ts, note) VALUES (?, ?)",
        params=(time.strftime("%Y-%m-%d %H:%M:%S"), "single insert"),
        commit=True,
    )

    # 批量插入（executemany，自动提交）
    sqlite_mod.batch_insert(
        db_path=db_path,
        sql="INSERT INTO heartbeat_log (ts, note) VALUES (?, ?)",
        rows=[
            (time.strftime("%Y-%m-%d %H:%M:%S"), "batch-1"),
            (time.strftime("%Y-%m-%d %H:%M:%S"), "batch-2"),
        ],
    )

    # 查询多行（rows 为 dict 列表）
    result = sqlite_mod.query(
        db_path=db_path,
        sql="SELECT COUNT(*) AS total FROM heartbeat_log",
    )
    if result["success"]:
        log(f"[示例3 sqlite-helper] 累计记录数={result['data']['rows'][0]['total']}")

    # 查询单行（row 为 dict 或 None）
    result = sqlite_mod.query_one(
        db_path=db_path,
        sql="SELECT * FROM heartbeat_log ORDER BY id DESC LIMIT 1",
    )
    if result["success"] and result["data"]["row"]:
        log(f"[示例3 sqlite-helper] 最新记录={result['data']['row']}")


# ═══════════════════════════════════════════════════════════════
# 4. mysql-helper — MySQL 读写模块
#    公开方法：execute(host, port, user, password, database, sql, params, commit)
#    特点：连接参数由调用方传入（跨库复用），内部连接池 + DictCursor
# ═══════════════════════════════════════════════════════════════

def demo_mysql_helper(modules, config, log):
    mysql_mod = modules.get("mysql-helper")
    if not mysql_mod:
        log("[示例4 mysql-helper] 模块未启用，跳过", "WARNING")
        return

    mysql_cfg = config.get("demo", {}).get("mysql", {})
    if not mysql_cfg.get("host"):
        log("[示例4 mysql-helper] 未配置 [demo.mysql].host，跳过真实调用。"
            "调用方式: mysql_mod.execute(host=..., port=..., user=..., "
            "password=..., database=..., sql='SELECT 1', params=None)")
        return

    # 查询示例：%s 占位符传参，返回 dict 行列表
    result = mysql_mod.execute(
        host=mysql_cfg["host"],
        port=mysql_cfg.get("port", 3306),
        user=mysql_cfg.get("user", ""),
        password=mysql_cfg.get("password", ""),
        database=mysql_cfg.get("database", ""),
        sql="SELECT NOW() AS now_time, %s AS tag",
        params=("heartbeat-demo",),
    )
    if result["success"]:
        log(f"[示例4 mysql-helper] 查询成功: {result['data']}")
    else:
        log(f"[示例4 mysql-helper] 查询失败: {result['error']}", "WARNING")


# ═══════════════════════════════════════════════════════════════
# 5. idaas-eiam — 阿里云 IDaaS EIAM API 模块
#    公开方法：list_users / list_applications / update_user /
#              authorize_application_to_users
#    特点：AK/SK 与 instance_id 在模块配置 [eiam] 段中设置，未配置时调用返回错误
# ═══════════════════════════════════════════════════════════════

def demo_idaas_eiam(modules, log):
    eiam_mod = modules.get("idaas-eiam")
    if not eiam_mod:
        log("[示例5 idaas-eiam] 模块未启用，跳过", "WARNING")
        return

    # 按显示名查询用户（未配置 AK/SK 时返回 success=False，不会抛异常中断服务）
    try:
        result = eiam_mod.list_users(display_name="demo")
        if result.get("success"):
            users = result.get("data", {}).get("users", [])
            log(f"[示例5 idaas-eiam] list_users 成功，匹配 {len(users)} 个用户")
        else:
            log(f"[示例5 idaas-eiam] list_users 未成功（通常为未配置 AK/SK）: "
                f"{result.get('error')}")
    except Exception as e:
        log(f"[示例5 idaas-eiam] 调用异常: {e}", "WARNING")


# ═══════════════════════════════════════════════════════════════
# 6. web-service — 统一 HTTP 服务器模块
#    公开方法：register_page / register_api / register_handler /
#              unregister / get_port
#    特点：多服务共享守护进程（默认 8910 端口），路由自动加 /{服务名} 前缀
# ═══════════════════════════════════════════════════════════════

def demo_web_service(modules, config, log):
    web_mod = modules.get("web-service")
    if not web_mod:
        log("[示例6 web-service] 模块未启用，跳过", "WARNING")
        return

    started_at = time.strftime("%Y-%m-%d %H:%M:%S")

    # 注册静态页面：GET /heartbeat/
    web_mod.register_page("/", (
        "<html><body><h1>Heartbeat 示例服务</h1>"
        "<p>本页面由 web-service 模块的 register_page 注册。</p>"
        "<p>JSON API 示例：<a href='/heartbeat/api/status'>/heartbeat/api/status</a></p>"
        "</body></html>"
    ))

    # 注册 JSON API：GET /heartbeat/api/status（callback 无参，后台线程每 5 秒刷新）
    web_mod.register_api("/api/status", lambda: {
        "service": "heartbeat",
        "started_at": started_at,
        "interval_seconds": config.get("interval", {}).get("seconds", 10),
    })

    # 注册自定义处理器：POST /heartbeat/echo（实时回调，可读请求体）
    def handle_echo(request_info):
        return {
            "status_code": 200,
            "content_type": "application/json; charset=utf-8",
            "body": json.dumps({"echo": request_info.get("body")}, ensure_ascii=False),
        }
    web_mod.register_handler("/echo", "POST", handle_echo)

    # 查询监听端口与带前缀的访问地址
    port_info = web_mod.get_port()
    if port_info["success"]:
        log(f"[示例6 web-service] 路由已注册，访问地址: {port_info['data']['base_url']}")


# ═══════════════════════════════════════════════════════════════
# 服务生命周期入口
# ═══════════════════════════════════════════════════════════════

def run(config, modules):
    """服务主入口：启动时依次执行各模块示例，之后进入心跳循环。

    Args:
        config: 来自 config.toml 的配置字典
        modules: 模块命名空间字典（仅包含已绑定且启用的模块）
    """
    log_mod = modules.get("log-enhancer")

    def _log(msg, level="INFO"):
        if log_mod:
            log_mod.log(msg, level=level)
        else:
            getattr(logger, level.lower(), logger.info)(msg)

    _log("═" * 20 + " 内置模块调用示例开始 " + "═" * 20)

    demo_log_enhancer(modules)
    demo_http_client(modules, _log)
    demo_sqlite_helper(modules, _log)
    demo_mysql_helper(modules, config, _log)
    demo_idaas_eiam(modules, _log)
    demo_web_service(modules, config, _log)

    _log("═" * 20 + " 内置模块调用示例结束 " + "═" * 20)

    # ── 心跳主循环 ──
    interval = config.get("interval", {}).get("seconds", 10)
    message = config.get("message", {}).get("text", "Hello from heartbeat")

    while True:
        _log(f"[heartbeat] {message} (interval={interval}s)")
        time.sleep(interval)


def on_config_reload(new_config):
    """配置热重载回调（interval/message 下一轮循环生效需重启，此处仅记录）"""
    logger.info("配置已重新加载")


def on_shutdown():
    """服务关闭回调"""
    logger.info("Heartbeat 示例服务正在关闭")
