"""Apollo 配置中心安全监控服务

定时扫描 Apollo 配置中心数据库，查找包含敏感信息的明文配置项，
支持自动加密、告警推送和心跳检测。

依赖模块：mysql-helper, ss-omc-sdk, kms-client, log-enhancer
"""
import logging
import time

logger = logging.getLogger(__name__)


def results_to_markdown(data, exclude_fields=None):
    """将查询结果格式化为 Markdown 表格"""
    if not data:
        return "查询结果为空"

    if exclude_fields is None:
        exclude_fields = ["value"]

    headers = [h for h in data[0].keys() if h not in exclude_fields]
    if not headers:
        return "所有字段都被排除，无可显示内容"

    markdown = "| " + " | ".join(headers) + " |\n"
    markdown += "| " + " | ".join(["---"] * len(headers)) + " |\n"

    for row in data:
        values = [str(row[h]) for h in headers]
        markdown += "| " + " | ".join(values) + " |\n"

    return markdown


def run(config, modules):
    """服务主入口。

    Args:
        config: 来自 config.toml 的配置字典
        modules: 模块命名空间字典
    """
    interval = config.get("interval", {}).get("minutes", 1)
    auto_encrypt = config.get("monitor", {}).get("auto_encrypt", False)

    mysql_mod = modules.get("mysql-helper")
    kms_mod = modules.get("kms-client")
    log_mod = modules.get("log-enhancer")
    omc_mod = modules.get("ss-omc-sdk")

    if not mysql_mod:
        logger.error("mysql-helper 模块为必需模块")
        return

    if not omc_mod:
        logger.warning("ss-omc-sdk 模块未启用，告警和心跳功能将不可用")

    def _log(msg, level="INFO"):
        if log_mod:
            log_mod.log(msg, level=level)
        else:
            getattr(logger, level.lower(), logger.info)(msg)

    def get_db_config(section="mysql"):
        """从配置中获取解密后的数据库连接参数"""
        db = config.get(section, {})
        user = db.get("user", "")
        password = db.get("password", "")
        if kms_mod and user.startswith("ECODE"):
            user = kms_mod.decrypt(user)
        if kms_mod and password.startswith("ECODE"):
            password = kms_mod.decrypt(password)
        return {
            "host": db.get("host", "127.0.0.1"),
            "port": db.get("port", 3306),
            "user": user,
            "password": password,
            "database": db.get("database", ""),
        }

    def send_alert(content):
        """发送告警通知"""
        if not omc_mod:
            logger.warning("ss-omc-sdk 未启用，无法发送告警")
            return

        alert_config = config.get("alert", {})
        from_value = alert_config.get("org_alert_name", "B级-SEC-Apollo明文监控(JST)")
        name = alert_config.get("alert_name", "Apollo明文监控")

        result = omc_mod.alarm_push(
            from_value=from_value,
            name=name,
            alarm_type="alert",
            msg=content,
        )
        if result.get("success"):
            _log("告警发送成功")
        else:
            _log(f"告警发送失败: {result.get('error', result.get('message', 'unknown'))}", "ERROR")

    def send_heartbeat():
        """发送心跳"""
        if not omc_mod:
            logger.warning("ss-omc-sdk 未启用，无法发送心跳")
            return

        alert_config = config.get("alert", {})
        from_value = alert_config.get("org_alert_name", "B级-SEC-Apollo明文监控(JST)")
        name = alert_config.get("alert_name", "Apollo明文监控")

        result = omc_mod.heartbeat_push(
            from_value=from_value,
            name=name,
        )
        if result.get("success"):
            _log("心跳发送成功")
        else:
            _log(f"心跳发送失败: {result.get('error', result.get('message', 'unknown'))}", "ERROR")

    def do_monitor_job():
        """执行监控任务"""
        db_params = get_db_config("mysql")
        sql_query = config.get("monitor", {}).get("sql_query", "")
        exclude_fields = config.get("monitor", {}).get("exclude_fields", ["value"])

        if not sql_query:
            _log("sql_query 未配置，跳过监控", "WARN")
            return

        _log("开始执行监控查询")
        result = mysql_mod.execute(
            **db_params,
            sql=sql_query,
        )

        if not result.get("success"):
            _log(f"数据库查询失败: {result.get('error', 'unknown')}", "ERROR")
            return

        data = result.get("rows", [])
        if not data:
            _log("查询结果为空，无需告警")
            return

        _log(f"查询到 {len(data)} 条记录")

        # 自动加密
        if auto_encrypt and kms_mod:
            encrypt_config = config.get("monitor", {}).get("encrypt_config", {})
            table = encrypt_config.get("table", "item")
            value_field = encrypt_config.get("value_field", "value")
            id_field = encrypt_config.get("id_field", "id")
            key_field = encrypt_config.get("key_field", "key")

            _log("开始执行自动加密")
            for row in data:
                try:
                    encrypted_value = kms_mod.encrypt(str(row[value_field]))
                    update_sql = (
                        f"UPDATE `{table}` SET `{value_field}`=%s "
                        f"WHERE `{id_field}`=%s AND `{key_field}`=%s"
                    )
                    mysql_mod.execute(
                        **db_params,
                        sql=update_sql,
                        params=(encrypted_value, row[id_field], row[key_field]),
                        commit=True,
                    )
                except Exception as e:
                    _log(f"加密失败 (id={row.get(id_field)}): {e}", "ERROR")
            _log("自动加密完成")

        # 发送告警
        markdown_table = results_to_markdown(data, exclude_fields)
        _log("开始发送告警")
        send_alert(markdown_table)

    # ── 主循环 ──
    _log("Apollo Monitor 服务启动")

    last_heartbeat_time = 0  # 启动时立即发送首次心跳
    heartbeat_interval_sec = interval * 3600  # 每 interval 小时发送一次心跳

    while True:
        # 执行监控任务
        try:
            do_monitor_job()
        except Exception as e:
            _log(f"监控任务异常: {e}", "ERROR")

        # 判断是否需要发送心跳
        now = time.time()
        if now - last_heartbeat_time >= heartbeat_interval_sec:
            try:
                send_heartbeat()
                last_heartbeat_time = now
            except Exception as e:
                _log(f"心跳发送异常: {e}", "ERROR")

        time.sleep(interval * 60)


def on_config_reload(new_config):
    """配置热重载回调"""
    logger.info("配置已重新加载")


def on_shutdown():
    """服务关闭回调"""
    logger.info("Apollo Monitor 服务正在关闭")