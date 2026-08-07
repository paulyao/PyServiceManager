"""Qoder 账号管理服务

管理 Qoder 和 Qoder CN 两个组织的账号列表、用量监控、共享资源包统计和 AI 代码统计。
提供对外 API 按审批流程自动分配账号，Web 页面展示账号差异和用量数据。

功能模块：
1. 账号管理：SQLite 存储 qoder_accounts/qoder_cn_accounts，预置 99 个 email 槽位
2. 对外 API：POST /api/allocate 按 appId 分配账号
3. Web 页面：4 个 tab（Qoder 账号 / Qoder CN 账号 / 用量监控 / AI 代码统计）
4. 用量监控：成员配额 + 共享资源包，两套配置
5. AI 代码统计：成员排名 + commit 明细 + 趋势图表

依赖模块：http-client, ss-omc-sdk, log-enhancer, web-service, sqlite-helper
"""
import io
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_ALARM_DEDUP_FILE = os.path.join(os.path.dirname(__file__), ".alarm_dedup.json")
_DB_FILE = os.path.join(os.path.dirname(__file__), ".qoder.db")
POLL_INTERVAL = 10

_data_lock = threading.Lock()
_shared_data = {
    "members": [],
    "members_cn": [],
    "last_updated": None,
    "total_members": 0,
    "exhausted_count": 0,
    "refresh_interval": 60,
    "ai_code_last_updated": None,
    "api_members": {},
    "api_members_cn": {},
}

_DASHBOARD_HTML = ""
try:
    with open(os.path.join(os.path.dirname(__file__), "dashboard.html"), "r", encoding="utf-8") as _f:
        _DASHBOARD_HTML = _f.read()
except Exception as e:
    logger.warning("Failed to load dashboard.html: %s", e)


def _next_run_timestamp(schedule_cfg, after=None):
    """计算下次执行的 Unix 时间戳。"""
    now = after if after is not None else time.time()
    now_dt = datetime.fromtimestamp(now)

    cron_expr = schedule_cfg.get("cron")
    if cron_expr:
        try:
            from croniter import croniter
            parts = cron_expr.strip().split()
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

def run(config, modules):
    """服务主入口。"""
    http_mod = modules.get("http-client")
    omc_mod = modules.get("ss-omc-sdk")
    log_mod = modules.get("log-enhancer")
    sqlite_mod = modules.get("sqlite-helper")

    if not http_mod:
        logger.error("http-client 模块为必需模块，请先绑定")
        return
    if not sqlite_mod:
        logger.error("sqlite-helper 模块为必需模块，请先绑定")
        return
    if not omc_mod:
        logger.warning("ss-omc-sdk 模块未启用，告警和心跳功能将不可用")

    qoder_cfg = config.get("qoder", {})
    api_base_url = qoder_cfg.get("api_base_url", "https://api.qoder.com")
    api_key = qoder_cfg.get("api_key", "")
    org_id = qoder_cfg.get("organization_id", "")

    cn_cfg = config.get("qoder_cn", {})
    cn_base_url = cn_cfg.get("api_base_url", "https://api.qoder.cn")
    cn_api_key = cn_cfg.get("api_key", "")
    cn_org_id = cn_cfg.get("organization_id", "")

    schedule_cfg = config.get("schedule", {})
    monitor_cfg = config.get("monitor", {})
    rate_limit_delay = monitor_cfg.get("rate_limit_delay", 0.5)
    alarm_dedup_days = monitor_cfg.get("alarm_dedup_days", 1)

    heartbeat_cfg = config.get("heartbeat", {})
    heartbeat_interval_sec = heartbeat_cfg.get("interval_minutes", 10) * 60

    accounts_cfg = config.get("accounts", {})
    app_id_cn = accounts_cfg.get("app_id_cn", 56)
    app_id_global = accounts_cfg.get("app_id_global", 57)
    excel_qoder_path = accounts_cfg.get("excel_qoder", "")
    excel_qoder_cn_path = accounts_cfg.get("excel_qoder_cn", "")

    _shared_data["refresh_interval"] = monitor_cfg.get("refresh_interval", 60)
    ai_code_days = config.get("ai_code", {}).get("days", 30)

    def _log(msg, level="INFO"):
        if log_mod:
            log_mod.log(msg, level=level)
        else:
            getattr(logger, level.lower(), logger.info)(msg)

    # ── SQLite 初始化 ──

    def _init_db():
        """初始化所有 SQLite 表。"""
        # 迁移：检测旧表结构，若有 used_value 列则删除重建
        try:
            cols = sqlite_mod.query(db_path=_DB_FILE, sql="PRAGMA table_info(quota_members)")
            col_names = [c.get("name", "") for c in cols.get("data", {}).get("rows", [])]
            if col_names and "plan_used" not in col_names:
                sqlite_mod.execute(db_path=_DB_FILE, sql="DROP TABLE IF EXISTS quota_members", commit=True)
                _log("检测到旧 quota_members 表结构，已删除重建")
        except Exception:
            pass  # 表不存在或查询失败，忽略
        sqlite_mod.execute(db_path=_DB_FILE, sql="""CREATE TABLE IF NOT EXISTS quota_members (
            name TEXT, email TEXT PRIMARY KEY,
            plan_used REAL, plan_limit REAL,
            pkg_used REAL, pkg_limit REAL,
            total_used REAL, total_limit REAL,
            shared_used REAL, shared_limit REAL,
            unit TEXT,
            status TEXT, next_reset_at TEXT, collected_at TEXT)""", commit=True)
        sqlite_mod.execute(db_path=_DB_FILE, sql="""CREATE TABLE IF NOT EXISTS quota_members_cn (
            name TEXT, email TEXT PRIMARY KEY,
            plan_used REAL, plan_limit REAL,
            pkg_used REAL, pkg_limit REAL,
            total_used REAL, total_limit REAL,
            shared_used REAL, shared_limit REAL,
            unit TEXT,
            status TEXT, next_reset_at TEXT, collected_at TEXT)""", commit=True)
        sqlite_mod.execute(db_path=_DB_FILE, sql="""CREATE TABLE IF NOT EXISTS qoder_accounts (
            email TEXT PRIMARY KEY, name TEXT DEFAULT '', department TEXT DEFAULT '',
            role TEXT DEFAULT '开发', updated_at TEXT)""", commit=True)
        sqlite_mod.execute(db_path=_DB_FILE, sql="""CREATE TABLE IF NOT EXISTS qoder_cn_accounts (
            email TEXT PRIMARY KEY, name TEXT DEFAULT '', department TEXT DEFAULT '',
            role TEXT DEFAULT '营销', updated_at TEXT)""", commit=True)
        # 迁移：旧账号表补充 role 列（默认角色：qoder=开发，qoder_cn=营销）
        for _tbl, _default_role in (("qoder_accounts", "开发"), ("qoder_cn_accounts", "营销")):
            try:
                cols = sqlite_mod.query(db_path=_DB_FILE, sql=f"PRAGMA table_info({_tbl})")
                col_names = [c.get("name", "") for c in cols.get("data", {}).get("rows", [])]
                if col_names and "role" not in col_names:
                    sqlite_mod.execute(db_path=_DB_FILE,
                        sql=f"ALTER TABLE {_tbl} ADD COLUMN role TEXT DEFAULT '{_default_role}'", commit=True)
                    _log(f"{_tbl} 已新增 role 列，默认 {_default_role}")
                # 早期迁移写入默认开发的历史数据，CN 表统一改为营销
                if _tbl == "qoder_cn_accounts":
                    sqlite_mod.execute(db_path=_DB_FILE,
                        sql=f"UPDATE {_tbl} SET role = '营销' WHERE role IS NULL OR role = '' OR role = '开发'", commit=True)
            except Exception as e:
                _log(f"{_tbl} role 列迁移失败: {e}", "WARN")
        sqlite_mod.execute(db_path=_DB_FILE, sql="""CREATE TABLE IF NOT EXISTS ai_code_ranking (
            user_id TEXT, email TEXT, display_name TEXT,
            total_lines_added INTEGER, ai_lines_added INTEGER,
            ai_share_rate REAL, commit_count INTEGER, collected_at TEXT)""", commit=True)
        sqlite_mod.execute(db_path=_DB_FILE, sql="""CREATE TABLE IF NOT EXISTS ai_code_trend_cache (
            days INTEGER, primary_branch_only INTEGER,
            items_json TEXT, next_items_json TEXT, collected_at TEXT,
            PRIMARY KEY (days, primary_branch_only))""", commit=True)
        sqlite_mod.execute(db_path=_DB_FILE, sql="""CREATE TABLE IF NOT EXISTS ai_code_commits (
            commit_hash TEXT PRIMARY KEY,
            user_id TEXT, user_email TEXT, repo_name TEXT, branch_name TEXT,
            is_primary_branch INTEGER,
            total_lines_added INTEGER, total_lines_deleted INTEGER,
            ide_next_added INTEGER, ide_next_deleted INTEGER,
            plugin_next_added INTEGER, plugin_next_deleted INTEGER,
            ide_agent_added INTEGER, ide_agent_deleted INTEGER,
            plugin_agent_added INTEGER, plugin_agent_deleted INTEGER,
            cli_agent_added INTEGER, cli_agent_deleted INTEGER,
            ide_quest_added INTEGER, ide_quest_deleted INTEGER,
            ide_inline_chat_added INTEGER, ide_inline_chat_deleted INTEGER,
            jb_inline_chat_added INTEGER, jb_inline_chat_deleted INTEGER,
            non_ai_added INTEGER, non_ai_deleted INTEGER,
            message TEXT, commit_ts TEXT, created_at TEXT, collected_at TEXT)""", commit=True)

    def _init_preset_accounts():
        """预置 99 个 email 槽位到两张账号表。"""
        qoder_rows = [(f"ai{i:02d}@wsgjp.com", "", "", None) for i in range(1, 100)]
        cn_rows = [(f"ai_cn{i:02d}@wsgjp.com", "", "", None) for i in range(1, 100)]
        sqlite_mod.batch_insert(db_path=_DB_FILE,
            sql="INSERT OR IGNORE INTO qoder_accounts (email, name, department, updated_at) VALUES (?,?,?,?)", rows=qoder_rows)
        sqlite_mod.batch_insert(db_path=_DB_FILE,
            sql="INSERT OR IGNORE INTO qoder_cn_accounts (email, name, department, updated_at) VALUES (?,?,?,?)", rows=cn_rows)
        _log(f"预置账号: qoder {len(qoder_rows)} 条, qoder_cn {len(cn_rows)} 条")

    def _import_excel(excel_path, table_name):
        """首次导入 Excel 数据到账号表。"""
        if not excel_path or not os.path.exists(excel_path):
            _log(f"Excel 文件不存在: {excel_path}", "WARN")
            return
        # 检查是否已有非空 name（已导入过）
        check = sqlite_mod.query(db_path=_DB_FILE,
            sql=f"SELECT COUNT(*) AS cnt FROM {table_name} WHERE name != '' AND name IS NOT NULL")
        if check.get("data", {}).get("rows", [{}])[0].get("cnt", 0) > 0:
            _log(f"{table_name} 已有 Excel 数据，跳过导入")
            return
        try:
            from openpyxl import load_workbook
            wb = load_workbook(excel_path, read_only=True)
            ws = wb.worksheets[0]
            rows_data = []
            for row in ws.iter_rows(min_row=2, values_only=True):
                email = row[0] if len(row) > 0 else None
                name = row[1] if len(row) > 1 else None
                dept = row[2] if len(row) > 2 else None
                if email and name:
                    rows_data.append((str(name).strip(), str(dept or "").strip(),
                                      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                      str(email).strip()))
            wb.close()
            if rows_data:
                sqlite_mod.batch_insert(db_path=_DB_FILE,
                    sql=f"UPDATE {table_name} SET name=?, department=?, updated_at=? WHERE email=?",
                    rows=rows_data)
                _log(f"Excel 导入 {table_name}: {len(rows_data)} 条记录")
        except Exception as e:
            _log(f"Excel 导入 {table_name} 失败: {e}", "WARN")

    # ── API 调用 ──

    def list_all_members(base_url, key, o_id):
        """分页获取组织下所有活跃成员。"""
        url = f"{base_url}/v1/organizations/{o_id}/members"
        headers = {"Authorization": f"Bearer {key}"}
        all_members = []
        next_token = ""
        while True:
            params = {"maxResults": 100}
            if next_token:
                params["nextToken"] = next_token
            result = http_mod.get(url, params=params, headers=headers)
            if not result.get("success"):
                _log(f"获取成员列表失败: {result.get('error', 'unknown')}", "ERROR")
                break
            data = result.get("json", {})
            all_members.extend(data.get("members", []))
            next_token = data.get("nextToken", "")
            if not next_token:
                break
        return all_members

    def get_member_quota(base_url, key, o_id, member_id):
        """查询指定成员的配额使用情况。"""
        url = f"{base_url}/v1/organizations/{o_id}/members/{member_id}/quota"
        headers = {"Authorization": f"Bearer {key}"}
        result = http_mod.get(url, headers=headers)
        if not result.get("success"):
            return None
        return result.get("json")

    def is_quota_exhausted(quota):
        if quota.get("status") == "restricted":
            return True
        total = quota.get("totalQuota", {}).get("quotaSummary", {})
        used = total.get("usedValue", 0)
        limit = total.get("limitValue", 0)
        return limit > 0 and used >= limit

    def format_alarm_message(member, quota):
        total = quota.get("totalQuota", {}).get("quotaSummary", {})
        used = total.get("usedValue", 0)
        limit = total.get("limitValue", 0)
        unit = total.get("unit", "credits")
        usage_pct = (used / limit * 100) if limit > 0 else 0
        return (
            "Qoder 成员配额耗尽告警\n\n"
            "| 字段 | 值 |\n| --- | --- |\n"
            f"| 成员 | {member.get('name', 'N/A')} |\n"
            f"| 邮箱 | {member.get('email', 'N/A')} |\n"
            f"| 状态 | {quota.get('status', 'unknown')} |\n"
            f"| 已使用 | {used:.2f} {unit} |\n"
            f"| 配额上限 | {limit:.2f} {unit} |\n"
            f"| 使用率 | {usage_pct:.1f}% |\n"
            f"| 检测时间 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |\n"
        )

    # ── 告警去重 ──

    def _load_alarm_dedup():
        try:
            if os.path.exists(_ALARM_DEDUP_FILE):
                with open(_ALARM_DEDUP_FILE, "r") as f:
                    return json.load(f)
        except Exception as e:
            _log(f"加载告警去重文件失败: {e}", "WARN")
        return {}

    def _save_alarm_dedup(records):
        try:
            with open(_ALARM_DEDUP_FILE, "w") as f:
                json.dump(records, f, indent=2)
        except Exception as e:
            _log(f"保存告警去重文件失败: {e}", "WARN")

    def _already_alerted(member_id):
        cutoff = datetime.now() - timedelta(days=alarm_dedup_days)
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        records = _load_alarm_dedup()
        alerted_date = records.get(member_id)
        return alerted_date is not None and alerted_date >= cutoff_str

    def _mark_alerted(member_id):
        today = datetime.now().strftime("%Y-%m-%d")
        records = _load_alarm_dedup()
        cutoff = datetime.now() - timedelta(days=alarm_dedup_days)
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        records = {k: v for k, v in records.items() if v >= cutoff_str}
        records[member_id] = today
        _save_alarm_dedup(records)

    def send_alarm(member, quota):
        member_id = member.get("id", "")
        if _already_alerted(member_id):
            _log(f"成员 {member.get('name', member_id)} 在去重窗口内已告警，跳过")
            return
        if not omc_mod:
            _log("ss-omc-sdk 未启用，跳过告警发送", "WARN")
            return
        alert_cfg = config.get("alert", {})
        msg = format_alarm_message(member, quota)
        result = omc_mod.alarm_push(
            from_value=alert_cfg.get("from", "Qoder账号管理"),
            name=alert_cfg.get("name", "Qoder配额耗尽告警"),
            alarm_type="alert", msg=msg)
        if result.get("success"):
            _log(f"告警发送成功: {member.get('name', member_id)}")
            _mark_alerted(member_id)
        else:
            _log(f"告警发送失败 ({member.get('name', member_id)}): {result.get('error', 'unknown')}", "ERROR")

    def send_heartbeat():
        if not omc_mod:
            return
        alert_cfg = config.get("alert", {})
        result = omc_mod.heartbeat_push(
            from_value=alert_cfg.get("from", "Qoder账号管理"),
            name=alert_cfg.get("name", "Qoder账号管理"))
        if result.get("success"):
            _log("心跳发送成功")
        else:
            _log(f"心跳发送失败: {result.get('error', 'unknown')}", "ERROR")

    # ── 采集任务 ──

    def do_check():
        """执行一轮完整的用量检查（两套配置），结果写入 SQLite。"""
        _log("开始检查成员用量")
        collected_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Qoder 国际版
        qoder_quota_data = _check_org_members(api_base_url, api_key, org_id)
        _save_quota_to_sqlite("quota_members", qoder_quota_data, collected_at)
        with _data_lock:
            _shared_data["members"] = qoder_quota_data
            _shared_data["last_updated"] = collected_at
            _shared_data["total_members"] = len(qoder_quota_data)
            _shared_data["exhausted_count"] = sum(1 for m in qoder_quota_data if m["status"] == "restricted")
            # 从用量表构建差异比对映射
            _shared_data["api_members"] = {m.get("email", ""): m.get("name", "") for m in qoder_quota_data}

        # Qoder CN
        cn_quota_data = _check_org_members(cn_base_url, cn_api_key, cn_org_id) if cn_api_key and cn_org_id else []
        if cn_quota_data:
            _save_quota_to_sqlite("quota_members_cn", cn_quota_data, collected_at)
            with _data_lock:
                _shared_data["members_cn"] = cn_quota_data
                _shared_data["api_members_cn"] = {m.get("email", ""): m.get("name", "") for m in cn_quota_data}

        _log(f"本轮检查完成: Qoder {len(qoder_quota_data)} 个成员, Qoder CN {len(cn_quota_data)} 个成员")

        # 同步采集 AI 代码统计
        try:
            do_ai_code_check()
        except Exception as e:
            _log(f"AI 代码统计采集异常: {e}", "ERROR")

    def _save_quota_to_sqlite(table_name, quota_data, collected_at):
        """将用量数据批量写入 SQLite。"""
        try:
            sqlite_mod.execute(db_path=_DB_FILE, sql=f"DELETE FROM {table_name}", commit=True)
            rows = [(m["name"], m["email"], m["planUsed"], m["planLimit"],
                     m["pkgUsed"], m["pkgLimit"], m["totalUsed"], m["totalLimit"],
                     m["sharedUsed"], m["sharedLimit"], m["unit"], m["status"], m["nextResetAt"], collected_at)
                    for m in quota_data]
            sqlite_mod.batch_insert(db_path=_DB_FILE,
                sql=f"INSERT OR REPLACE INTO {table_name} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows=rows)
            _log(f"用量数据已写入 {table_name}: {len(rows)} 条记录")
        except Exception as e:
            _log(f"写入 {table_name} 失败: {e}", "WARN")

    def _check_org_members(base_url, key, o_id):
        """检查单个组织的成员配额，包括 Plan/资源包/总计/共享包配额。"""
        members = list_all_members(base_url, key, o_id)
        if not members:
            _log(f"组织 {o_id} 未获取到任何成员", "WARN")
            return []
        _log(f"组织 {o_id}: 共 {len(members)} 个成员，开始检查配额")
        quota_data = []
        for member in members:
            member_id = member.get("id")
            if not member_id:
                continue
            quota = get_member_quota(base_url, key, o_id, member_id)
            if quota is None:
                continue
            plan_q = quota.get("planQuota", {}).get("quotaSummary", {})
            pkg_q = quota.get("resourcePackageQuota", {}).get("quotaSummary", {})
            total_q = quota.get("totalQuota", {}).get("quotaSummary", {})
            shared_q = quota.get("sharedQuota", {}).get("quotaSummary", {})
            quota_data.append({
                "name": member.get("name", ""),
                "email": member.get("email", ""),
                "planUsed": plan_q.get("usedValue", 0),
                "planLimit": plan_q.get("limitValue", 0),
                "pkgUsed": pkg_q.get("usedValue", 0),
                "pkgLimit": pkg_q.get("limitValue", 0),
                "totalUsed": total_q.get("usedValue", 0),
                "totalLimit": total_q.get("limitValue", 0),
                "sharedUsed": shared_q.get("usedValue", 0),
                "sharedLimit": shared_q.get("limitValue", 0),
                "unit": total_q.get("unit", "credits"),
                "status": quota.get("status", "unknown"),
                "nextResetAt": quota.get("nextResetAt", ""),
            })
            # 自动清理：距下次重置不足 24h 且总计已用=0 的成员，调用 API 删除
            next_reset = quota.get("nextResetAt", "")
            total_used_val = total_q.get("usedValue", 0)
            if next_reset and total_used_val == 0:
                try:
                    from datetime import timezone
                    reset_dt = datetime.fromisoformat(next_reset.replace("Z", "+00:00"))
                    now_utc = datetime.now(timezone.utc)
                    hours_left = (reset_dt - now_utc).total_seconds() / 3600
                    if 0 <= hours_left < 24:
                        del_headers = {"Authorization": f"Bearer {key}"}
                        del_result = http_mod.delete(
                            f"{base_url}/v1/organizations/{o_id}/members/{member_id}",
                            headers=del_headers)
                        if del_result.get("success"):
                            _log(f"自动删除成员: {member.get('email', '')} "
                                 f"(距重置 {hours_left:.1f}h, 总计已用=0)")
                        else:
                            _log(f"自动删除失败: {member.get('email', '')} - "
                                 f"{del_result.get('error', 'unknown')}", "WARN")
                except Exception as e:
                    _log(f"自动删除检查异常: {member.get('email', '')} - {e}", "WARN")
            if is_quota_exhausted(quota):
                send_alarm(member, quota)
            time.sleep(rate_limit_delay)
        return quota_data

    def do_ai_code_check():
        """采集 AI 代码排名和 commit 明细，存入 SQLite。"""
        _log("开始采集 AI 代码统计")
        headers = {"Authorization": f"Bearer {api_key}"}
        now_dt = datetime.now()
        end_date = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        start_date = (now_dt - timedelta(days=ai_code_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        collected_at = now_dt.strftime("%Y-%m-%d %H:%M:%S")

        # API 1: 成员排名
        ranking_url = f"{api_base_url}/v1/organizations/{org_id}/ai-code/stats/member-ranking"
        ranking_result = http_mod.get(ranking_url, params={
            "start_date": start_date, "end_date": end_date, "limit": 200}, headers=headers)
        if ranking_result.get("success"):
            items = ranking_result.get("json", {}).get("items", [])
            sqlite_mod.execute(db_path=_DB_FILE, sql="DELETE FROM ai_code_ranking", commit=True)
            for item in items:
                sqlite_mod.execute(db_path=_DB_FILE,
                    sql="INSERT INTO ai_code_ranking VALUES (?,?,?,?,?,?,?,?)",
                    params=(item.get("userId", ""), item.get("email", ""), item.get("displayName", ""),
                            item.get("totalLinesAdded", 0), item.get("aiLinesAdded", 0),
                            item.get("aiShareRate", 0), item.get("commitCount", 0), collected_at), commit=True)
            _log(f"AI 代码排名: {len(items)} 个成员")
        else:
            _log(f"获取 AI 代码排名失败: {ranking_result.get('error', 'unknown')}", "WARN")

        # API 2: Commit 明细（分页）
        commits_url = f"{api_base_url}/v1/organizations/{org_id}/ai-code-tracking/commits"
        page = 1
        total_pages = 1
        total_commits = 0
        while page <= total_pages:
            result = http_mod.get(commits_url, params={
                "startDate": start_date, "endDate": end_date,
                "page": page, "pageSize": 200}, headers=headers)
            if not result.get("success"):
                break
            data = result.get("json", {}).get("data", {})
            items = data.get("items", [])
            pagination = data.get("pagination", {})
            total_pages = pagination.get("totalPages", 1)
            for item in items:
                sqlite_mod.execute(db_path=_DB_FILE,
                    sql="INSERT OR REPLACE INTO ai_code_commits VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    params=(item.get("commitHash", ""), item.get("userId", ""), item.get("userEmail", ""),
                            item.get("repoName", ""), item.get("branchName", ""), int(item.get("isPrimaryBranch", False)),
                            item.get("totalLinesAdded", 0), item.get("totalLinesDeleted", 0),
                            item.get("ideNextLinesAdded", 0), item.get("ideNextLinesDeleted", 0),
                            item.get("pluginNextLinesAdded", 0), item.get("pluginNextLinesDeleted", 0),
                            item.get("ideAgentLinesAdded", 0), item.get("ideAgentLinesDeleted", 0),
                            item.get("pluginAgentLinesAdded", 0), item.get("pluginAgentLinesDeleted", 0),
                            item.get("cliAgentLinesAdded", 0), item.get("cliAgentLinesDeleted", 0),
                            item.get("ideQuestLinesAdded", 0), item.get("ideQuestLinesDeleted", 0),
                            item.get("ideInlineChatLinesAdded", 0), item.get("ideInlineChatLinesDeleted", 0),
                            item.get("jbInlineChatLinesAdded", 0), item.get("jbInlineChatLinesDeleted", 0),
                            item.get("nonAiLinesAdded", 0), item.get("nonAiLinesDeleted", 0),
                            item.get("message", ""), item.get("commitTs", ""), item.get("createdAt", ""),
                            collected_at), commit=True)
            total_commits += len(items)
            page += 1
            time.sleep(rate_limit_delay)

        with _data_lock:
            _shared_data["ai_code_last_updated"] = collected_at
        _log(f"AI 代码统计采集完成: {total_commits} 条 commit 记录")

    # ── 从 SQLite 加载缓存数据 ──
    _init_db()
    _init_preset_accounts()
    _import_excel(excel_qoder_path, "qoder_accounts")
    _import_excel(excel_qoder_cn_path, "qoder_cn_accounts")

    try:
        result = sqlite_mod.query(db_path=_DB_FILE,
            sql="SELECT name, email, plan_used, plan_limit, pkg_used, pkg_limit, total_used, total_limit, shared_used, shared_limit, unit, status, next_reset_at, collected_at FROM quota_members")
        cached_members = []
        for r in result.get("data", {}).get("rows", []):
            cached_members.append({"name": r.get("name", ""), "email": r.get("email", ""),
                "planUsed": r.get("plan_used", 0), "planLimit": r.get("plan_limit", 0),
                "pkgUsed": r.get("pkg_used", 0), "pkgLimit": r.get("pkg_limit", 0),
                "totalUsed": r.get("total_used", 0), "totalLimit": r.get("total_limit", 0),
                "sharedUsed": r.get("shared_used", 0), "sharedLimit": r.get("shared_limit", 0),
                "unit": r.get("unit", "credits"), "status": r.get("status", "unknown"),
                "nextResetAt": r.get("next_reset_at", "")})
        if cached_members:
            with _data_lock:
                _shared_data["members"] = cached_members
                _shared_data["total_members"] = len(cached_members)
                _shared_data["exhausted_count"] = sum(1 for m in cached_members if m["status"] == "restricted")
                _shared_data["last_updated"] = cached_members[0].get("collected_at")
                _shared_data["api_members"] = {m.get("email", ""): m.get("name", "") for m in cached_members}
            _log(f"已从 SQLite 加载 Qoder 缓存: {len(cached_members)} 个成员")
    except Exception as e:
        _log(f"加载 Qoder SQLite 缓存失败: {e}", "WARN")

    # 恢复 Qoder CN 用量缓存
    try:
        cn_result = sqlite_mod.query(db_path=_DB_FILE,
            sql="SELECT name, email, plan_used, plan_limit, pkg_used, pkg_limit, total_used, total_limit, shared_used, shared_limit, unit, status, next_reset_at, collected_at FROM quota_members_cn")
        cn_cached = []
        for r in cn_result.get("data", {}).get("rows", []):
            cn_cached.append({"name": r.get("name", ""), "email": r.get("email", ""),
                "planUsed": r.get("plan_used", 0), "planLimit": r.get("plan_limit", 0),
                "pkgUsed": r.get("pkg_used", 0), "pkgLimit": r.get("pkg_limit", 0),
                "totalUsed": r.get("total_used", 0), "totalLimit": r.get("total_limit", 0),
                "sharedUsed": r.get("shared_used", 0), "sharedLimit": r.get("shared_limit", 0),
                "unit": r.get("unit", "credits"), "status": r.get("status", "unknown"),
                "nextResetAt": r.get("next_reset_at", "")})
        if cn_cached:
            with _data_lock:
                _shared_data["members_cn"] = cn_cached
                _shared_data["api_members_cn"] = {m.get("email", ""): m.get("name", "") for m in cn_cached}
            _log(f"已从 SQLite 加载 Qoder CN 缓存: {len(cn_cached)} 个成员")
    except Exception as e:
        _log(f"加载 Qoder CN SQLite 缓存失败: {e}", "WARN")

    try:
        ai_result = sqlite_mod.query_one(db_path=_DB_FILE,
            sql="SELECT collected_at FROM ai_code_ranking ORDER BY collected_at DESC LIMIT 1")
        if ai_result.get("success") and ai_result.get("data", {}).get("row"):
            with _data_lock:
                _shared_data["ai_code_last_updated"] = ai_result["data"]["row"]["collected_at"]
            _log(f"已恢复 AI 代码统计时间戳: {_shared_data['ai_code_last_updated']}")
    except Exception as e:
        _log(f"恢复 AI 代码统计时间戳失败: {e}", "WARN")

    # ── 注册 Web 路由 ──
    _log("Qoder 账号管理服务启动")

    web_mod = modules.get("web-service")
    if web_mod:
        web_mod.register_page("/", _DASHBOARD_HTML)

        def _get_accounts_snapshot(request_info):
            """返回两个组织的账号列表 + 资源包 + 差异标记。"""
            with _data_lock:
                api_members = dict(_shared_data.get("api_members", {}))
                api_members_cn = dict(_shared_data.get("api_members_cn", {}))
            return {"status_code": 200, "content_type": "application/json; charset=utf-8",
                    "body": json.dumps({
                        "qoder": _build_account_data("qoder_accounts", api_members),
                        "qoder_cn": _build_account_data("qoder_cn_accounts", api_members_cn),
                    }, ensure_ascii=False)}

        def _build_account_data(table_name, api_map):
            """构建单个组织的账号数据。"""
            acc_result = sqlite_mod.query(db_path=_DB_FILE,
                sql=f"SELECT email, name, department, role, updated_at FROM {table_name} ORDER BY email")
            members = []
            local_named_emails = set()
            for r in acc_result.get("data", {}).get("rows", []):
                email = r.get("email", "")
                name = r.get("name", "")
                if name:
                    local_named_emails.add(email)
                diff = None
                if name and email in api_map:
                    if api_map[email] and api_map[email] != name:
                        diff = "名称不一致"
                elif name and email not in api_map:
                    diff = "API无此账号"
                members.append({"email": email, "name": name,
                                "department": r.get("department", ""),
                                "role": r.get("role") or "开发",
                                "updated_at": r.get("updated_at") or "", "diff": diff})
            # API 有但本地没有（未分配）的账号也展示并标记差异
            for api_email, api_name in api_map.items():
                if api_email and api_email not in local_named_emails:
                    members.append({"email": api_email, "name": api_name or api_email,
                                    "department": "", "role": "", "updated_at": "",
                                    "diff": "本地无此账号"})
            return {"members": members, "summary": {"last_updated": _shared_data.get("last_updated")}}

        web_mod.register_handler("/api/accounts", "POST", _get_accounts_snapshot)

        def handle_account_update(request_info):
            """手工编辑账号的姓名和部门。"""
            body = request_info.get("body", "{}")
            try:
                data = json.loads(body) if isinstance(body, str) else body
            except json.JSONDecodeError:
                return {"status_code": 400, "content_type": "application/json; charset=utf-8",
                        "body": json.dumps({"success": False, "error": "Invalid JSON"}, ensure_ascii=False)}
            table_map = {"qoder": "qoder_accounts", "qoder_cn": "qoder_cn_accounts"}
            table = table_map.get(data.get("org", ""))
            email = (data.get("email") or "").strip()
            if not table or not email:
                return {"status_code": 400, "content_type": "application/json; charset=utf-8",
                        "body": json.dumps({"success": False, "error": "org 或 email 无效"}, ensure_ascii=False)}
            name = (data.get("name") or "").strip()
            department = (data.get("department") or "").strip()
            role = (data.get("role") or "开发").strip()
            if role not in ("开发", "测试", "运维", "产品", "营销"):
                return {"status_code": 400, "content_type": "application/json; charset=utf-8",
                        "body": json.dumps({"success": False, "error": f"不支持的角色: {role}"}, ensure_ascii=False)}
            # 本地表无此邮箱时先插入（如 API 有但本地没有的账号）
            sqlite_mod.execute(db_path=_DB_FILE,
                sql=f"INSERT OR IGNORE INTO {table} (email) VALUES (?)",
                params=(email,), commit=True)
            sqlite_mod.execute(db_path=_DB_FILE,
                sql=f"UPDATE {table} SET name = ?, department = ?, role = ?, updated_at = ? WHERE email = ?",
                params=(name, department, role,
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"), email), commit=True)
            _log(f"账号编辑: {table} {email} name={name} department={department} role={role}")
            return {"status_code": 200, "content_type": "application/json; charset=utf-8",
                    "body": json.dumps({"success": True}, ensure_ascii=False)}

        web_mod.register_handler("/api/account-update", "POST", handle_account_update)

        def handle_account_add(request_info):
            """手动新增账号：写入本地数据库。"""
            def _resp(payload):
                return {"status_code": 200, "content_type": "application/json; charset=utf-8",
                        "body": json.dumps(payload, ensure_ascii=False)}
            body = request_info.get("body", "{}")
            try:
                data = json.loads(body) if isinstance(body, str) else body
            except json.JSONDecodeError:
                return _resp({"success": False, "error": "Invalid JSON"})
            org = data.get("org", "")
            table_map = {"qoder": "qoder_accounts", "qoder_cn": "qoder_cn_accounts"}
            table = table_map.get(org)
            email = (data.get("email") or "").strip()
            if not table or not email:
                return _resp({"success": False, "error": "org 或邮箱无效"})
            name = (data.get("name") or "").strip()
            department = (data.get("department") or "").strip()
            role = (data.get("role") or "").strip() or ("营销" if org == "qoder_cn" else "开发")
            if role not in ("开发", "测试", "运维", "产品", "营销"):
                return _resp({"success": False, "error": f"不支持的角色: {role}"})
            # 校验本地是否已存在
            existing = sqlite_mod.query_one(db_path=_DB_FILE,
                sql=f"SELECT email, name FROM {table} WHERE email = ?", params=(email,))
            row = existing.get("data", {}).get("row")
            if row:
                if row.get("name"):
                    return _resp({"success": False, "error": f"该邮箱已被占用: {email}"})
                # 已有邮箱但姓名为空 → 更新记录（认领空闲槽位）
                sqlite_mod.execute(db_path=_DB_FILE,
                    sql=f"UPDATE {table} SET name = ?, department = ?, role = ?, updated_at = ? WHERE email = ?",
                    params=(name, department, role,
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S"), email), commit=True)
                _log(f"账号认领: {table} {email} name={name} department={department} role={role}")
                return _resp({"success": True})
            sqlite_mod.execute(db_path=_DB_FILE,
                sql=f"INSERT INTO {table} (email, name, department, role, updated_at) VALUES (?, ?, ?, ?, ?)",
                params=(email, name, department, role,
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S")), commit=True)
            _log(f"账号新增: {table} {email} name={name} department={department} role={role}")
            return _resp({"success": True})

        web_mod.register_handler("/api/account-add", "POST", handle_account_add)

        def handle_account_delete(request_info):
            """删除账号：API 无此账号时删除本地记录，其他情况仅调用 API 移除成员。"""
            def _resp(status_code, payload):
                return {"status_code": status_code, "content_type": "application/json; charset=utf-8",
                        "body": json.dumps(payload, ensure_ascii=False)}
            body = request_info.get("body", "{}")
            try:
                data = json.loads(body) if isinstance(body, str) else body
            except json.JSONDecodeError:
                return _resp(400, {"success": False, "error": "Invalid JSON"})
            org = data.get("org", "")
            email = (data.get("email") or "").strip()
            if org == "qoder":
                table, base_url, key, o_id = "qoder_accounts", api_base_url, api_key, org_id
            elif org == "qoder_cn":
                table, base_url, key, o_id = "qoder_cn_accounts", cn_base_url, cn_api_key, cn_org_id
            else:
                return _resp(400, {"success": False, "error": f"不支持的 org: {org}"})
            if not email:
                return _resp(400, {"success": False, "error": "email 不能为空"})
            # 按邮箱查询成员，判断 API 中是否存在
            headers = {"Authorization": f"Bearer {key}"}
            result = http_mod.get(f"{base_url}/v1/organizations/{o_id}/members",
                params={"email": email}, headers=headers)
            if not result.get("success"):
                return _resp(502, {"success": False,
                    "error": f"查询 API 成员失败: {result.get('error', 'unknown')}"})
            api_members_list = result.get("json", {}).get("members", [])
            if not api_members_list:
                # API 无此账号：删除本地记录
                sqlite_mod.execute(db_path=_DB_FILE,
                    sql=f"DELETE FROM {table} WHERE email = ?", params=(email,), commit=True)
                _log(f"账号删除: {table} {email} 本地记录已删除（API 无此账号）")
                return _resp(200, {"success": True,
                    "message": "API 中无此账号，已删除本地记录"})
            # API 存在：仅调用 API 移除成员，本地数据保留
            member_id = api_members_list[0].get("id", "")
            del_result = http_mod.delete(
                f"{base_url}/v1/organizations/{o_id}/members/{member_id}", headers=headers)
            if not del_result.get("success"):
                err = del_result.get("json", {}).get("message") or del_result.get("error", "unknown")
                return _resp(200, {"success": False, "error": f"API 删除成员失败: {err}"})
            _log(f"账号删除: {table} {email} member_id={member_id} 已从组织移除")
            # 同步移除差异比对映射，避免删除后仍显示差异
            key_name = "api_members" if org == "qoder" else "api_members_cn"
            with _data_lock:
                _shared_data[key_name].pop(email, None)
            return _resp(200, {"success": True,
                "message": "删除成功：成员已从组织移除，本地数据保留"})

        web_mod.register_handler("/api/account-delete", "POST", handle_account_delete)

        def handle_export_accounts(request_info):
            """导出账号列表为 Excel（xlsx 字节流）。"""
            body = request_info.get("body", "{}")
            try:
                data = json.loads(body) if isinstance(body, str) else body
            except json.JSONDecodeError:
                data = {}
            org = data.get("org", "")
            if org == "qoder":
                table, sheet_name = "qoder_accounts", "Qoder 账号"
                map_key = "api_members"
            elif org == "qoder_cn":
                table, sheet_name = "qoder_cn_accounts", "Qoder CN 账号"
                map_key = "api_members_cn"
            else:
                return {"status_code": 400, "content_type": "application/json; charset=utf-8",
                        "body": json.dumps({"success": False, "error": f"不支持的 org: {org}"}, ensure_ascii=False)}
            try:
                with _data_lock:
                    api_map = dict(_shared_data.get(map_key, {}))
                acc_data = _build_account_data(table, api_map)
                members = acc_data.get("members", [])
                from openpyxl import Workbook
                wb = Workbook()
                ws = wb.active
                ws.title = sheet_name
                ws.append(["邮箱", "姓名", "部门", "角色", "更新时间", "差异"])
                for m in members:
                    ws.append([m.get("email", ""), m.get("name", ""), m.get("department", ""),
                               m.get("role", ""), m.get("updated_at", ""), m.get("diff") or ""])
                for col, width in zip("ABCDEF", (32, 14, 18, 10, 20, 14)):
                    ws.column_dimensions[col].width = width
                buf = io.BytesIO()
                wb.save(buf)
                _log(f"导出账号列表 {sheet_name}: {len(members)} 条")
                return {"status_code": 200,
                        "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        "body": buf.getvalue()}
            except Exception as e:
                _log(f"导出账号列表失败: {e}", "ERROR")
                return {"status_code": 500, "content_type": "application/json; charset=utf-8",
                        "body": json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)}

        web_mod.register_handler("/api/export-accounts", "POST", handle_export_accounts)

        def handle_usage(request_info):
            """返回两个组织的完整用量数据（从 SQLite 读取）。"""
            with _data_lock:
                snapshot = dict(_shared_data)
            qoder_members = snapshot.get("members", [])
            cn_members = snapshot.get("members_cn", [])
            return {"status_code": 200, "content_type": "application/json; charset=utf-8",
                    "body": json.dumps({
                        "qoder": {"members": qoder_members},
                        "qoder_cn": {"members": cn_members},
                        "last_updated": snapshot.get("last_updated"),
                    }, ensure_ascii=False)}

        web_mod.register_handler("/api/usage", "POST", handle_usage)

        def handle_update_addon_cap(request_info):
            """修改成员共享额度上限（Add-On Cap），数值按百位取整。"""
            def _resp(status_code, payload):
                return {"status_code": status_code, "content_type": "application/json; charset=utf-8",
                        "body": json.dumps(payload, ensure_ascii=False)}
            body = request_info.get("body", "{}")
            try:
                data = json.loads(body) if isinstance(body, str) else body
            except json.JSONDecodeError:
                return _resp(400, {"success": False, "error": "Invalid JSON"})
            org = data.get("org", "")
            email = (data.get("email") or "").strip()
            if org == "qoder":
                base_url, key, o_id = api_base_url, api_key, org_id
            elif org == "qoder_cn":
                base_url, key, o_id = cn_base_url, cn_api_key, cn_org_id
            else:
                return _resp(400, {"success": False, "error": f"不支持的 org: {org}"})
            if not email:
                return _resp(400, {"success": False, "error": "email 不能为空"})
            try:
                cap = int(round(float(data.get("addOnCap")) / 100.0)) * 100
            except (TypeError, ValueError):
                return _resp(400, {"success": False, "error": "addOnCap 无效"})
            if cap < 0:
                return _resp(400, {"success": False, "error": "addOnCap 不能为负数"})
            headers = {"Authorization": f"Bearer {key}"}
            # 按邮箱精准查询成员 ID
            result = http_mod.get(f"{base_url}/v1/organizations/{o_id}/members",
                params={"email": email}, headers=headers)
            if not result.get("success"):
                return _resp(502, {"success": False, "error": f"查询成员失败: {result.get('error', 'unknown')}"})
            api_members_list = result.get("json", {}).get("members", [])
            if not api_members_list:
                return _resp(404, {"success": False, "error": f"未找到成员: {email}"})
            member_id = api_members_list[0].get("id", "")
            put_result = http_mod.put(
                f"{base_url}/v1/organizations/{o_id}/members/{member_id}/addon-cap",
                json={"addOnCap": cap}, headers=headers)
            if not put_result.get("success"):
                err = put_result.get("json", {}).get("message") or put_result.get("error", "unknown")
                return _resp(502, {"success": False, "error": f"更新 Add-On Cap 失败: {err}"})
            new_cap = put_result.get("json", {}).get("addOnCap", cap)
            _log(f"共享额度更新: {org} {email} addOnCap={new_cap}")
            return _resp(200, {"success": True, "addOnCap": new_cap})

        web_mod.register_handler("/api/update-addon-cap", "POST", handle_update_addon_cap)

        def handle_allocate(request_info):
            """对外账号分配 API。"""
            body = request_info.get("body", "{}")
            try:
                data = json.loads(body) if isinstance(body, str) else body
            except json.JSONDecodeError:
                return _alloc_response(False, 0, "Invalid JSON", None)
            oa = data.get("oaApproval", {})
            app_id = oa.get("appId")
            submitter_name = oa.get("submitterName", "")
            dept_list = data.get("departmentNameList", [])

            if app_id == app_id_cn:
                table = "qoder_cn_accounts"
            elif app_id == app_id_global:
                table = "qoder_accounts"
            else:
                return _alloc_response(False, 0, f"不支持的 appId: {app_id}", None)

            if not submitter_name:
                return _alloc_response(False, 0, "submitterName 不能为空", None)

            # 1. 按 name 匹配
            result = sqlite_mod.query_one(db_path=_DB_FILE,
                sql=f"SELECT email FROM {table} WHERE name = ?", params=(submitter_name,))
            if result.get("data", {}).get("row"):
                email = result["data"]["row"]["email"]
                _log(f"账号分配: appId={app_id}, name={submitter_name} → {email} (已有)")
                return _alloc_response(True, 1, None, email)

            # 2. 取 name 为空且 email 最小的
            result = sqlite_mod.query_one(db_path=_DB_FILE,
                sql=f"SELECT email FROM {table} WHERE (name = '' OR name IS NULL) ORDER BY email ASC LIMIT 1")
            if result.get("data", {}).get("row"):
                email = result["data"]["row"]["email"]
                dept_str = ";".join(dept_list)
                sqlite_mod.execute(db_path=_DB_FILE,
                    sql=f"UPDATE {table} SET name = ?, department = ?, updated_at = ? WHERE email = ?",
                    params=(submitter_name, dept_str,
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S"), email), commit=True)
                _log(f"账号分配: appId={app_id}, name={submitter_name} → {email} (新分配)")
                return _alloc_response(True, 1, None, email)

            return _alloc_response(False, 0, "无可用账号", None)

        def _alloc_response(success, code, message, email):
            data = [{"fieldName": "email", "fieldValue": email}] if email else []
            return {"status_code": 200, "content_type": "application/json; charset=utf-8",
                    "body": json.dumps({"message": message, "code": code, "data": data,
                        "success": success, "version": "1.0.3",
                        "timestamp": int(time.time() * 1000)}, ensure_ascii=False)}

        web_mod.register_handler("/api/allocate", "POST", handle_allocate)

        # AI 代码统计 API
        def handle_ai_code_stats(request_info):
            if not os.path.exists(_DB_FILE):
                return {"status_code": 200, "content_type": "application/json; charset=utf-8",
                        "body": json.dumps({"members": [], "last_updated": None}, ensure_ascii=False)}
            query = request_info.get("query", {})
            primary_only = query.get("primary_branch_only", ["false"])[0] == "true" if query.get("primary_branch_only") else False
            days = int(query.get("days", [str(ai_code_days)])[0]) if query.get("days") else ai_code_days
            cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")
            conditions = [f"commit_ts >= '{cutoff_date}'"]
            if primary_only:
                conditions.append("is_primary_branch = 1")
            where_clause = "WHERE " + " AND ".join(conditions)
            result = sqlite_mod.query(db_path=_DB_FILE, sql=f"""SELECT
                COALESCE(NULLIF(user_email, ''), user_id) AS member_key, user_email AS email,
                COUNT(*) AS commit_count, SUM(total_lines_added) AS total_added,
                SUM(total_lines_deleted) AS total_deleted,
                SUM(ide_next_added + plugin_next_added + ide_agent_added + plugin_agent_added +
                    cli_agent_added + ide_quest_added + ide_inline_chat_added + jb_inline_chat_added) AS ai_added,
                SUM(ide_next_deleted + plugin_next_deleted + ide_agent_deleted + plugin_agent_deleted +
                    cli_agent_deleted + ide_quest_deleted + ide_inline_chat_deleted + jb_inline_chat_deleted) AS ai_deleted,
                SUM(non_ai_added) AS non_ai_added, SUM(non_ai_deleted) AS non_ai_deleted,
                SUM(ide_next_added) AS ide_next_added, SUM(ide_agent_added) AS ide_agent_added,
                SUM(ide_quest_added) AS ide_quest_added, SUM(plugin_next_added) AS plugin_next_added,
                SUM(plugin_agent_added) AS plugin_agent_added, SUM(jb_inline_chat_added) AS jb_inline_chat_added,
                SUM(cli_agent_added) AS cli_agent_added
                FROM ai_code_commits {where_clause}
                GROUP BY COALESCE(NULLIF(user_email, ''), user_id)""")
            rows = result.get("data", {}).get("rows", [])
            rk_result = sqlite_mod.query(db_path=_DB_FILE,
                sql="SELECT email, display_name, ai_share_rate FROM ai_code_ranking")
            ranking_map = {r["email"]: r for r in rk_result.get("data", {}).get("rows", [])}
            members = []
            for r in rows:
                email = r.get("email") or ""
                rk = ranking_map.get(email, {})
                name = rk.get("display_name") if rk else (email or r.get("member_key"))
                ai_added = r.get("ai_added") or 0
                non_ai_added = r.get("non_ai_added") or 0
                ai_rate = (ai_added / (ai_added + non_ai_added) * 100) if (ai_added + non_ai_added) > 0 else 0
                members.append({"name": name, "email": email, "commit_count": r.get("commit_count"),
                    "total_added": r.get("total_added"), "total_deleted": r.get("total_deleted"),
                    "ai_added": ai_added, "ai_deleted": r.get("ai_deleted"),
                    "non_ai_added": non_ai_added, "non_ai_deleted": r.get("non_ai_deleted"),
                    "ide_next_added": r.get("ide_next_added") or 0, "ide_agent_added": r.get("ide_agent_added") or 0,
                    "ide_quest_added": r.get("ide_quest_added") or 0, "plugin_next_added": r.get("plugin_next_added") or 0,
                    "plugin_agent_added": r.get("plugin_agent_added") or 0, "jb_inline_chat_added": r.get("jb_inline_chat_added") or 0,
                    "cli_agent_added": r.get("cli_agent_added") or 0, "ai_rate": round(ai_rate, 1)})
            return {"status_code": 200, "content_type": "application/json; charset=utf-8",
                    "body": json.dumps({"members": members,
                        "last_updated": _shared_data.get("ai_code_last_updated")}, ensure_ascii=False)}

        web_mod.register_handler("/api/ai-code", "POST", handle_ai_code_stats)

        def handle_ai_code_trend(request_info):
            query = request_info.get("query", {})
            days = int(query.get("days", ["7"])[0]) if query.get("days") else 7
            primary_only = query.get("primary_branch_only", ["false"])[0] == "true" if query.get("primary_branch_only") else False
            primary_int = 1 if primary_only else 0
            cache_result = sqlite_mod.query_one(db_path=_DB_FILE,
                sql="SELECT items_json, next_items_json, collected_at FROM ai_code_trend_cache WHERE days = ? AND primary_branch_only = ?",
                params=(days, primary_int))
            if cache_result.get("success"):
                row = cache_result.get("data", {}).get("row")
                if row:
                    try:
                        cached_time = datetime.strptime(row.get("collected_at", ""), "%Y-%m-%d %H:%M:%S")
                        if (datetime.now() - cached_time).total_seconds() < 3600:
                            return {"status_code": 200, "content_type": "application/json; charset=utf-8",
                                    "body": json.dumps({"items": json.loads(row.get("items_json", "[]")),
                                        "nextItems": json.loads(row.get("next_items_json", "[]"))}, ensure_ascii=False)}
                    except Exception:
                        pass
            now_dt = datetime.now()
            end_date = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            start_date = (now_dt - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            url = f"{api_base_url}/v1/organizations/{org_id}/ai-code/stats/daily-trend"
            result = http_mod.get(url, params={"start_date": start_date, "end_date": end_date,
                "primary_branch_only": "true" if primary_only else "false"},
                headers={"Authorization": f"Bearer {api_key}"})
            if not result.get("success"):
                return {"status_code": 502, "content_type": "application/json; charset=utf-8",
                        "body": json.dumps({"error": result.get("error", "API error")}, ensure_ascii=False)}
            json_data = result.get("json", {})
            items = json_data.get("items", [])
            next_items = json_data.get("nextItems", [])
            collected_at = now_dt.strftime("%Y-%m-%d %H:%M:%S")
            try:
                sqlite_mod.execute(db_path=_DB_FILE,
                    sql="INSERT OR REPLACE INTO ai_code_trend_cache (days, primary_branch_only, items_json, next_items_json, collected_at) VALUES (?,?,?,?,?)",
                    params=(days, primary_int, json.dumps(items, ensure_ascii=False),
                            json.dumps(next_items, ensure_ascii=False), collected_at), commit=True)
            except Exception as e:
                _log(f"趋势数据缓存写入失败: {e}", "WARN")
            return {"status_code": 200, "content_type": "application/json; charset=utf-8",
                    "body": json.dumps({"items": items, "nextItems": next_items}, ensure_ascii=False)}

        web_mod.register_handler("/api/ai-code-trend", "POST", handle_ai_code_trend)

        port_info = web_mod.get_port()
        if port_info.get("success"):
            _log(f"Web 仪表盘: {port_info['data']['base_url']}")
    else:
        _log("web-service 模块未启用，Web 仪表盘不可用", "WARN")

    # ── 主循环 ──
    now = time.time()
    next_check_at = _next_run_timestamp(schedule_cfg, after=now)
    next_heartbeat_at = now
    _log(f"下次采集将在 {datetime.fromtimestamp(next_check_at).isoformat()} 执行")

    while True:
        now = time.time()
        if now >= next_check_at:
            try:
                do_check()
            except Exception as e:
                _log(f"检查任务异常: {e}", "ERROR")
            next_check_at = _next_run_timestamp(schedule_cfg)
            _log(f"下次采集将在 {datetime.fromtimestamp(next_check_at).isoformat()} 执行")
        if now >= next_heartbeat_at:
            try:
                send_heartbeat()
            except Exception as e:
                _log(f"心跳发送异常: {e}", "ERROR")
            next_heartbeat_at = time.time() + heartbeat_interval_sec
        time.sleep(POLL_INTERVAL)


def on_config_reload(new_config):
    logger.info("配置已重新加载")


def on_shutdown():
    logger.info("Qoder 账号管理服务正在关闭")
