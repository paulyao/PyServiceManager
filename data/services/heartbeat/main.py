"""内置模块使用参考示例

本文件展示了 pyservice-manager 所有内置模块的调用方式，
可作为新建服务时的参考模板。

内置模块列表：
  - log-enhancer: 增强日志输出（支持 INFO/WARN/ERROR 级别）
  - mysql-helper: MySQL 数据库操作（连接测试、单条/批量 SQL 执行）
  - http-client: 通用 HTTP 请求（GET/POST/PUT/DELETE）
"""
import logging
import time

logger = logging.getLogger(__name__)


def run(config, modules):
    """服务主入口。

    Args:
        config: 从 config.toml 解析的配置字典
        modules: 已启用模块的命名空间字典，通过 modules["模块名"].方法() 调用
    """

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 配置使用示例
    # config 对应 config.toml 中的内容，按 section 分组为嵌套字典
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    interval = config.get("interval", {}).get("seconds", 5)
    message = config.get("message", {}).get("text", "Hello from heartbeat")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # log-enhancer 模块使用示例
    # 提供增强的日志输出，支持 INFO / WARN / ERROR 三个级别
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if "log-enhancer" in modules:
        log = modules["log-enhancer"]

        # 普通信息日志（默认级别为 INFO）
        log.log("服务启动成功")
        log.log("这是一条 INFO 日志", level="INFO")

        # 警告日志
        log.log("这是一条警告信息", level="WARN")

        # 错误日志
        log.log("这是一条错误信息", level="ERROR")
    else:
        logger.info("log-enhancer 模块未启用，使用 logger 输出")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # mysql-helper 模块使用示例
    # 示例代码（需要有可用的 MySQL 连接才能运行）
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if "mysql-helper" in modules:
        mysql = modules["mysql-helper"]

        # MySQL 连接参数（请根据实际环境修改）
        db_config = {
            "host": "127.0.0.1",
            "port": 3306,
            "user": "root",
            "password": "your_password",
        }

        # --- 测试数据库连接 ---
        # result = mysql.test_connection(**db_config)
        # if result["success"]:
        #     logger.info("MySQL 连接成功")
        # else:
        #     logger.info(f"MySQL 连接失败: {result['error']}")

        # --- 执行 SELECT 查询 ---
        # result = mysql.execute(
        #     **db_config,
        #     database="test_db",
        #     sql="SELECT id, name FROM users WHERE status = %s",
        #     params=(1,),
        # )
        # if result["success"]:
        #     for row in result["rows"]:
        #         logger.info(f"  用户: {row}")
        #     logger.info(f"  共 {result['rowcount']} 条记录")
        # else:
        #     logger.info(f"查询失败: {result['error']}")

        # --- 执行 INSERT（写操作需设 commit=True）---
        # result = mysql.execute(
        #     **db_config,
        #     database="test_db",
        #     sql="INSERT INTO users (name, email) VALUES (%s, %s)",
        #     params=("张三", "zhangsan@example.com"),
        #     commit=True,
        # )
        # if result["success"]:
        #     logger.info(f"插入成功, lastrowid={result['lastrowid']}")
        # else:
        #     logger.info(f"插入失败: {result['error']}")

        # --- 批量执行 SQL（execute_many）---
        # result = mysql.execute_many(
        #     **db_config,
        #     database="test_db",
        #     sql="INSERT INTO users (name, email) VALUES (%s, %s)",
        #     params_list=[
        #         ("李四", "lisi@example.com"),
        #         ("王五", "wangwu@example.com"),
        #     ],
        # )
        # if result["success"]:
        #     logger.info(f"批量插入成功, 影响 {result['rowcount']} 行")
        # else:
        #     logger.info(f"批量插入失败: {result['error']}")

        logger.info("mysql-helper 模块已加载（示例代码已注释，取消注释即可运行）")
    else:
        logger.info("mysql-helper 模块未启用")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # http-client 模块使用示例
    # 通用 HTTP 请求模块，支持 GET/POST/PUT/DELETE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if "http-client" in modules:
        http = modules["http-client"]

        # --- GET 请求 ---
        # result = http.get(
        #     "https://api.example.com/users",
        #     params={"page": 1, "limit": 10},
        # )
        # if result["success"]:
        #     logger.info(f"GET 状态码: {result['status_code']}")
        #     logger.info(f"响应 JSON: {result['json']}")
        # else:
        #     logger.info(f"GET 请求失败: {result['error']}")

        # --- POST JSON 请求 ---
        # result = http.post(
        #     "https://api.example.com/users",
        #     json={"name": "张三", "email": "zhangsan@example.com"},
        # )
        # if result["success"]:
        #     logger.info(f"POST 状态码: {result['status_code']}")
        #     logger.info(f"创建结果: {result['json']}")
        # else:
        #     logger.info(f"POST 请求失败: {result['error']}")

        # --- 带自定义 Headers 的请求 ---
        # result = http.get(
        #     "https://api.example.com/protected/resource",
        #     headers={
        #         "Authorization": "Bearer your_token_here",
        #         "X-Custom-Header": "custom_value",
        #     },
        # )
        # if result["success"]:
        #     logger.info(f"认证请求成功: {result['status_code']}")
        # else:
        #     logger.info(f"认证请求失败: {result['error']}")

        # --- 错误处理示例 ---
        result = http.get("http://127.0.0.1:8900", timeout=5)
        if not result["success"]:
            logger.info(f"请求错误 - 状态码: {result['status_code']}, 错误: {result['error']}")

        logger.info(f"状态码: {result['status_code']}")
    else:
        logger.info("http-client 模块未启用")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 心跳循环（服务核心逻辑）
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    while True:
        if "log-enhancer" in modules:
            modules["log-enhancer"].log(f"{message}", level="INFO")
        else:
            logger.info(message)
        time.sleep(interval)


def on_config_reload(new_config):
    """配置热重载回调，当 config.toml 被修改时自动调用。"""
    logger.info("配置已重新加载")


def on_shutdown():
    """服务停止回调，用于清理资源。"""
    logger.info("服务正在关闭")
