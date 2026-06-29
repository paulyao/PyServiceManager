"""Grafana 仪表盘每日快照服务

每天定时调用 Grafana API 获取指定仪表盘的 JSON 模型，将时间范围固定为昨天整天，
然后创建仪表盘快照，用于保存历史仪表盘状态。

调度策略：严格按 cron 表达式绝对时钟执行。启动时先检查昨天快照是否存在，
不存在则立即生成，然后等待首个 cron 触发时间点。
croniter 未安装时回退到 run_hour 整点调度（每日指定小时执行）。

时间范围处理：将昨天 00:00:00.000 ~ 23:59:59.999（本地时区）转换为毫秒级 Unix
时间戳，写入 dashboard.time 字段，确保快照时间范围固定不随时间漂移。

依赖模块：http-client
"""
import logging
import time
from datetime import datetime, time as dtime, timedelta

logger = logging.getLogger(__name__)

POLL_INTERVAL = 10

# 配置热重载持有器：on_config_reload 更新此字典，主循环每轮读取最新配置
_config_holder = {"config": None}


def _next_run_timestamp(schedule_cfg, after=None):
    """计算下次执行的 Unix 时间戳。

    优先使用 croniter 解析 cron 表达式；未安装或解析失败时回退到 run_hour
    整点调度（每日指定小时 00 分执行）。

    Args:
        schedule_cfg: schedule 配置节字典。
        after: 基准时间戳，默认为当前时间。

    Returns:
        float: 下次执行时间的 Unix 时间戳。
    """
    now = after if after is not None else time.time()
    now_dt = datetime.fromtimestamp(now)

    cron_expr = schedule_cfg.get("cron")
    if cron_expr:
        try:
            from croniter import croniter

            parts = cron_expr.strip().split()
            # 兼容 6 段 cron（含秒），取后 5 段
            if len(parts) == 6:
                cron_expr = " ".join(parts[1:])
            cron = croniter(cron_expr, now_dt)
            next_dt = cron.get_next(datetime)
            logger.info("cron 调度: %s，下次执行 %s", schedule_cfg.get("cron"), next_dt.isoformat())
            return next_dt.timestamp()
        except ImportError:
            logger.warning("croniter 未安装，回退到 run_hour 调度")
        except Exception as e:
            logger.warning("cron 解析失败，回退到 run_hour: %s", e)

    run_hour = schedule_cfg.get("run_hour", 9)
    next_dt = now_dt.replace(hour=run_hour, minute=0, second=0, microsecond=0)
    if next_dt <= now_dt:
        next_dt += timedelta(days=1)
    logger.info("每日调度: %02d:00，下次执行 %s", run_hour, next_dt.isoformat())
    return next_dt.timestamp()


def _yesterday_time_range():
    """计算昨天整天的毫秒级 Unix 时间戳范围（本地时区）。

    时间范围为昨天 00:00:01 到 23:59:59（含秒，不含毫秒）。

    Returns:
        tuple: (from_ms, to_ms, yesterday_str)
            from_ms: 昨天 00:00:01 的毫秒时间戳字符串
            to_ms: 昨天 23:59:59 的毫秒时间戳字符串
            yesterday_str: 昨天日期字符串，格式 YYYY-MM-DD
    """
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    start = datetime.combine(yesterday, dtime(0, 0, 1))  # 昨天 00:00:01
    end = datetime.combine(yesterday, dtime(23, 59, 59))  # 昨天 23:59:59
    from_ms = str(int(start.timestamp() * 1000))
    to_ms = str(int(end.timestamp() * 1000))
    return from_ms, to_ms, yesterday.strftime("%Y-%m-%d")


def _fetch_dashboard(http_mod, base_url, api_token, dashboard_uid, org_id=1):
    """通过 Grafana API 获取仪表盘 JSON 模型。

    调用 GET /api/dashboards/uid/:uid，返回 {"meta": {...}, "dashboard": {...}}。

    Args:
        http_mod: http-client 模块实例。
        base_url: Grafana 服务地址。
        api_token: Grafana API Token。
        dashboard_uid: 目标仪表盘 UID。
        org_id: 组织 ID。

    Returns:
        dict or None: dashboard 模型字典，失败返回 None。
    """
    url = f"{base_url.rstrip('/')}/api/dashboards/uid/{dashboard_uid}"
    headers = {"Authorization": f"Bearer {api_token}"}
    params = {"orgId": org_id}

    result = http_mod.get(url, params=params, headers=headers, timeout=30)
    if not result.get("success"):
        logger.error(
            "获取仪表盘失败 [UID=%s]: HTTP %s - %s",
            dashboard_uid,
            result.get("status_code"),
            result.get("error", "unknown"),
        )
        return None

    data = result.get("json", {})
    dashboard = data.get("dashboard")
    if not dashboard:
        logger.error("仪表盘响应缺少 dashboard 字段: %s", str(result.get("body", ""))[:200])
        return None

    logger.info("成功获取仪表盘 [UID=%s, title=%s]", dashboard_uid, dashboard.get("title", "N/A"))
    return dashboard


def _create_snapshot(http_mod, base_url, api_token, dashboard, name, expires=0, org_id=1):
    """创建 Grafana 仪表盘快照。

    将昨天的绝对时间戳写入 dashboard.time 字段后，调用 POST /api/snapshots。
    时间范围固定为昨天 00:00:00.000 ~ 23:59:59.999，确保快照不随时间漂移。

    Args:
        http_mod: http-client 模块实例。
        base_url: Grafana 服务地址。
        api_token: Grafana API Token。
        dashboard: 仪表盘模型字典（会被修改 time 字段）。
        name: 快照名称。
        expires: 过期时间（秒），0 表示永不过期。
        org_id: 组织 ID。

    Returns:
        dict or None: 快照响应 {"key", "url", "deleteKey", "deleteUrl"}，失败返回 None。
    """
    from_ms, to_ms, _ = _yesterday_time_range()
    # 固定时间范围，防止快照时间随当前时间漂移
    dashboard["time"] = {"from": from_ms, "to": to_ms}
    # 设置时区为 browser，使 Grafana 按查看者浏览器时区显示时间范围
    dashboard["timezone"] = "browser"

    # post() 不支持 params，orgId 拼接到 URL
    url = f"{base_url.rstrip('/')}/api/snapshots"
    if org_id:
        url = f"{url}?orgId={org_id}"
    headers = {"Authorization": f"Bearer {api_token}"}
    payload = {
        "dashboard": dashboard,
        "name": name,
        "expires": expires,
    }

    result = http_mod.post(url, json=payload, headers=headers, timeout=60)
    if not result.get("success"):
        logger.error(
            "创建快照失败: HTTP %s - %s",
            result.get("status_code"),
            result.get("error", "unknown"),
        )
        return None

    snapshot = result.get("json", {})
    key = snapshot.get("key", "")
    snapshot_url = snapshot.get("url", "")
    if not snapshot_url:
        logger.error("快照响应缺少 url 字段: %s", str(result.get("body", ""))[:200])
        return None

    logger.info("快照创建成功 [key=%s]", key)
    logger.info("快照 URL: %s", snapshot_url)
    return snapshot


def _check_snapshot_exists(http_mod, base_url, api_token, snapshot_name, org_id=1):
    """检查指定名称的快照是否已存在。

    调用 GET /api/dashboard/snapshots?query=<name>&limit=10 搜索快照列表，
    若有 name 完全匹配的记录则返回 True。API 调用失败时返回 False（fail-safe，
    失败则继续创建，避免因查询故障导致快照遗漏）。

    Args:
        http_mod: http-client 模块实例。
        base_url: Grafana 服务地址。
        api_token: Grafana API Token。
        snapshot_name: 要检查的快照名称。
        org_id: 组织 ID。

    Returns:
        bool: 快照已存在返回 True，不存在或查询失败返回 False。
    """
    url = f"{base_url.rstrip('/')}/api/dashboard/snapshots"
    headers = {"Authorization": f"Bearer {api_token}"}
    params = {"query": snapshot_name, "limit": 10}

    result = http_mod.get(url, params=params, headers=headers, timeout=30)
    if not result.get("success"):
        logger.warning(
            "查询快照列表失败: HTTP %s - %s，将继续创建",
            result.get("status_code"),
            result.get("error", "unknown"),
        )
        return False

    snapshots = result.get("json", [])
    if not isinstance(snapshots, list):
        return False

    for snap in snapshots:
        if snap.get("name") == snapshot_name:
            logger.info("快照已存在: %s (key=%s)", snapshot_name, snap.get("key", ""))
            return True

    return False


def _do_snapshot(config, http_mod):
    """执行一次完整的快照流程：获取仪表盘 -> 设置时间范围 -> 创建快照。

    Args:
        config: 当前配置字典（支持热重载后的最新配置）。
        http_mod: http-client 模块实例。

    Returns:
        bool: 成功返回 True，失败返回 False。
    """
    grafana_cfg = config.get("grafana", {})
    snapshot_cfg = config.get("snapshot", {})

    base_url = grafana_cfg.get("base_url", "")
    api_token = grafana_cfg.get("api_token", "")
    dashboard_uid = grafana_cfg.get("dashboard_uid", "")
    org_id = grafana_cfg.get("org_id", 1)
    name_prefix = snapshot_cfg.get("name_prefix", "Grafana 仪表盘快照")
    expires = snapshot_cfg.get("expires", 0)

    if not all([base_url, api_token, dashboard_uid]):
        logger.error("配置缺失: base_url, api_token, dashboard_uid 为必填项")
        return False

    # 快照名称: {name_prefix} {YYYY-MM-DD}，日期为昨天
    _, _, yesterday_str = _yesterday_time_range()
    snapshot_name = f"{name_prefix} {yesterday_str}"
    logger.info("开始生成快照: %s", snapshot_name)

    # 1. 获取仪表盘 JSON
    dashboard = _fetch_dashboard(http_mod, base_url, api_token, dashboard_uid, org_id)
    if dashboard is None:
        return False

    # 2. 创建快照（内部设置昨天时间范围）
    snapshot = _create_snapshot(
        http_mod, base_url, api_token, dashboard, snapshot_name, expires, org_id
    )
    if snapshot is None:
        return False

    logger.info("快照流程完成: %s -> %s", snapshot_name, snapshot.get("url", ""))
    return True


def run(config, modules):
    """服务主入口。

    Args:
        config: config.toml 解析后的配置字典。
        modules: 已启用模块的命名空间字典，通过 modules["http-client"] 访问。
    """
    # 存储配置供热重载使用
    _config_holder["config"] = config

    http_mod = modules.get("http-client")
    if not http_mod:
        logger.error("http-client 模块为必需模块，请先在平台绑定")
        return

    logger.info("Grafana 仪表盘快照服务启动")
    logger.info("Grafana 地址: %s", config.get("grafana", {}).get("base_url", ""))
    logger.info("目标仪表盘 UID: %s", config.get("grafana", {}).get("dashboard_uid", ""))

    # 启动时检查昨天的快照是否已存在，不存在则立即生成
    current_config = _config_holder["config"] or config
    grafana_cfg = current_config.get("grafana", {})
    snapshot_cfg = current_config.get("snapshot", {})
    name_prefix = snapshot_cfg.get("name_prefix", "Grafana 仪表盘快照")
    _, _, yesterday_str = _yesterday_time_range()
    expected_name = f"{name_prefix} {yesterday_str}"

    if _check_snapshot_exists(
        http_mod,
        grafana_cfg.get("base_url", ""),
        grafana_cfg.get("api_token", ""),
        expected_name,
        grafana_cfg.get("org_id", 1),
    ):
        logger.info("昨天快照已存在，跳过启动补生成: %s", expected_name)
    else:
        logger.info("昨天快照不存在，启动补生成: %s", expected_name)
        try:
            _do_snapshot(current_config, http_mod)
        except Exception as e:
            logger.error("启动补生成快照异常: %s", e, exc_info=True)

    # 计算首次执行时间
    schedule_cfg = current_config.get("schedule", {})
    now = time.time()
    next_run_at = _next_run_timestamp(schedule_cfg, after=now)
    logger.info("下次快照将在 %s 执行", datetime.fromtimestamp(next_run_at).isoformat())

    # 主循环
    while True:
        now = time.time()

        # 每轮读取最新配置（支持热重载后的调度变更）
        current_config = _config_holder["config"] or config

        if now >= next_run_at:
            try:
                _do_snapshot(current_config, http_mod)
            except Exception as e:
                logger.error("快照任务异常: %s", e, exc_info=True)
            # 以当前时间重新计算下次触发
            current_schedule = current_config.get("schedule", {})
            next_run_at = _next_run_timestamp(current_schedule)
            logger.info("下次快照将在 %s 执行", datetime.fromtimestamp(next_run_at).isoformat())

        time.sleep(POLL_INTERVAL)


def on_config_reload(new_config):
    """配置热重载回调。

    更新配置持有器，使主循环在下一轮读取最新配置。
    调度时间、Grafana 连接参数等变更将在下一轮周期自动生效。
    """
    _config_holder["config"] = new_config
    grafana_cfg = new_config.get("grafana", {})
    schedule_cfg = new_config.get("schedule", {})
    logger.info(
        "配置已热重载: base_url=%s, dashboard_uid=%s, cron=%s",
        grafana_cfg.get("base_url", ""),
        grafana_cfg.get("dashboard_uid", ""),
        schedule_cfg.get("cron", ""),
    )


def on_shutdown():
    """服务关闭回调。"""
    logger.info("Grafana 仪表盘快照服务正在关闭")
