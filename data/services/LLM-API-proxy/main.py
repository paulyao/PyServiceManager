"""LLM API 代理服务

将 DashScope（OpenAI 兼容模式）的大模型能力封装为平台 HTTP 服务，
提供通用 LLM 代理接口和 JAR 包 license 查询接口（移植自原 DeepSeek Flask 项目）。

API（通过 web-service 模块提供，URL 自动添加服务名前缀）：
- GET  /             → 健康检查
- POST /chat         → 通用 LLM 代理（messages 必填，model/temperature/max_tokens 可选）
- POST /get-license  → 查询 JAR 包 license 信息（jar_name 必填，返回 JSON 数组）

依赖模块：log-enhancer, web-service
依赖包：openai
"""
import json
import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)

# 共享配置引用，on_config_reload 时整体替换，handler 闭包读取最新值
_state = {"config": {}}

# JAR license 查询 prompt（移植自原项目 query2api.py）
_LICENSE_PROMPT = (
    "{jar_name} 查找并按json格式输出这些 jar 包的license 信息：[{{'jar'：'xxx',"
    "'license':'xxx','fromurl':'xxx','mavenURL':'xxx'}}]，fromurl "
    "是指 JAR 包的 License信息来源URL（即官方或权威来源的 URL）,mavenurl"
    "指在Maven仓库中搜索的url，忽略版本号（例如：https://central.sonatype.com"
    "/search?q=nio-multipart-parser或https://mvnrepository.com/search?q=nio-multipart-parser），在 maven"
    "仓库中搜索不到的只返回 jar，其他字段返回空值。结果只返回json串不要包含其他说明"
)


def _json_response(status_code, body):
    """构造 JSON 响应。"""
    return {
        "status_code": status_code,
        "content_type": "application/json; charset=utf-8",
        "body": json.dumps(body, ensure_ascii=False),
    }


def _parse_body(request_info):
    """从 request_info 解析 JSON 请求体。

    Returns:
        tuple: (data: dict|None, error_response: dict|None)
    """
    body = request_info.get("body", "{}") or "{}"
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        return None, _json_response(400, {
            "success": False, "data": None, "error": f"invalid JSON: {e}"
        })
    if not isinstance(data, dict):
        return None, _json_response(400, {
            "success": False, "data": None, "error": "请求体必须是 JSON 对象"
        })
    return data, None


def _new_request_id():
    """生成毫秒级请求 ID（沿用原项目格式）。"""
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]


def call_llm(messages, log, model=None, temperature=None, max_tokens=None):
    """调用 DashScope OpenAI 兼容接口，带指数退避重试。

    Args:
        messages: OpenAI 格式消息列表
        log: 日志函数 log(msg, level)
        model/temperature/max_tokens: 可选覆盖，默认取配置 [llm] 段

    Returns:
        dict: {"content": str, "usage": dict, "model": str}

    Raises:
        ValueError: api_key 未配置
        Exception: 重试耗尽后抛出最后一次异常
    """
    from openai import OpenAI

    cfg = _state["config"]
    llm_cfg = cfg.get("llm", {})
    retry_cfg = cfg.get("retry", {})

    api_key = llm_cfg.get("api_key", "")
    if not api_key:
        raise ValueError("api_key 未配置，请在 config.toml [llm] 段填写")

    params = {
        "model": model or llm_cfg.get("model", "deepseek-v3"),
        "messages": messages,
        "stream": False,
    }
    temp = temperature if temperature is not None else llm_cfg.get("temperature")
    if temp is not None:
        params["temperature"] = temp
    mt = max_tokens if max_tokens is not None else llm_cfg.get("max_tokens", 0)
    if mt:
        params["max_tokens"] = mt

    max_retries = retry_cfg.get("max_retries", 3)
    retry_delay = retry_cfg.get("retry_delay", 2)

    for attempt in range(max_retries):
        try:
            client = OpenAI(
                api_key=api_key,
                base_url=llm_cfg.get(
                    "base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"
                ),
            )
            response = client.chat.completions.create(**params)
            content = response.choices[0].message.content
            usage = {}
            if response.usage:
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }
            return {"content": content, "usage": usage, "model": response.model}
        except Exception as e:
            if attempt < max_retries - 1:
                log(f"LLM 调用失败(第{attempt + 1}次): {e}，{retry_delay}s 后重试", "WARNING")
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                raise


def get_license_info(jar_name, log):
    """查询 JAR 包 license 信息（移植自原项目 query2api.py）。

    Returns:
        list: license 信息列表

    Raises:
        ValueError: 模型返回内容不是有效 JSON
    """
    prompt = _LICENSE_PROMPT.format(jar_name=jar_name)
    result = call_llm([{"role": "user", "content": prompt}], log)

    # 剥离 markdown 代码围栏后解析 JSON
    cleaned = result["content"].replace("```json", "").replace("```", "").strip()
    try:
        license_info = json.loads(cleaned)
    except json.JSONDecodeError:
        raise ValueError("返回的内容不是有效的JSON格式")

    # 统一包装为列表，保持原接口返回格式兼容
    if not isinstance(license_info, list):
        license_info = [license_info] if license_info else []
    return license_info


def run(config, modules):
    """服务主入口

    Args:
        config: 来自 config.toml 的配置字典
        modules: 模块命名空间字典
    """
    _state["config"] = config

    log_mod = modules.get("log-enhancer")

    def _log(msg, level="INFO"):
        if log_mod:
            log_mod.log(msg, level=level)
        else:
            getattr(logger, level.lower(), logger.info)(msg)

    # ── Web 路由处理器 ──

    def handle_health():
        """GET / — 健康检查（register_api 无参回调，返回 dict 自动包装）"""
        llm_cfg = _state["config"].get("llm", {})
        return {
            "status": "ok",
            "model": llm_cfg.get("model", "deepseek-v3"),
            "api_key_configured": bool(llm_cfg.get("api_key")),
        }

    def handle_chat(request_info):
        """POST /chat — 通用 LLM 代理"""
        request_id = _new_request_id()
        _log(f"[REQUEST_START] ID={request_id} path=/chat")
        try:
            data, err_resp = _parse_body(request_info)
            if err_resp:
                return err_resp

            messages = data.get("messages")
            if not isinstance(messages, list) or not messages:
                _log(f"[REQUEST_VALIDATION] ID={request_id} Error: messages 必须是非空列表", "WARNING")
                return _json_response(400, {
                    "success": False, "data": None, "error": "messages 参数必须是非空列表"
                })

            result = call_llm(
                messages, _log,
                model=data.get("model"),
                temperature=data.get("temperature"),
                max_tokens=data.get("max_tokens"),
            )
            _log(f"[QUERY_SUCCESS] ID={request_id} model={result['model']} usage={result['usage']}")
            return _json_response(200, {"success": True, "data": result, "error": None})

        except ValueError as ve:
            _log(f"[CONFIG_ERROR] ID={request_id} Error={ve}", "ERROR")
            return _json_response(503, {"success": False, "data": None, "error": str(ve)})
        except Exception as e:
            _log(f"[UNEXPECTED_ERROR] ID={request_id} ErrorType={type(e).__name__} ErrorMsg={e}", "ERROR")
            return _json_response(500, {"success": False, "data": None, "error": f"服务内部错误: {e}"})
        finally:
            _log(f"[REQUEST_END] ID={request_id}")

    def handle_get_license(request_info):
        """POST /get-license — JAR 包 license 查询（返回格式与原 Flask 接口兼容）"""
        request_id = _new_request_id()
        _log(f"[REQUEST_START] ID={request_id} path=/get-license")
        jar_name = "unknown"
        try:
            data, err_resp = _parse_body(request_info)
            if err_resp:
                return err_resp

            jar_name = data.get("jar_name") or "unknown"
            if not data.get("jar_name"):
                _log(f"[REQUEST_VALIDATION] ID={request_id} Error: jar_name参数是必需的", "WARNING")
                return _json_response(400, [{"error": "jar_name参数是必需的"}])

            _log(f"[QUERY_START] ID={request_id} jar_name={jar_name}")
            license_info = get_license_info(jar_name, _log)
            _log(f"[QUERY_SUCCESS] ID={request_id} 结果数量={len(license_info)}")
            return _json_response(200, license_info)

        except ValueError as ve:
            _log(f"[VALUE_ERROR] ID={request_id} Error={ve}", "ERROR")
            return _json_response(503, [{"jar": jar_name, "error": str(ve)}])
        except Exception as e:
            _log(f"[UNEXPECTED_ERROR] ID={request_id} ErrorType={type(e).__name__} ErrorMsg={e}", "ERROR")
            return _json_response(500, [{"jar": jar_name, "error": f"服务内部错误: {e}"}])
        finally:
            _log(f"[REQUEST_END] ID={request_id}")

    # ── 注册 Web 路由（通过 web-service 模块） ──
    web_mod = modules.get("web-service")
    if web_mod:
        web_mod.register_api("/", handle_health)
        web_mod.register_handler("/chat", "POST", handle_chat)
        web_mod.register_handler("/get-license", "POST", handle_get_license)

        port_info = web_mod.get_port()
        if port_info.get("success"):
            _log(f"LLM API 代理服务已启动: {port_info['data']['base_url']}")
    else:
        _log("web-service 模块未启用，HTTP 接口不可用", "ERROR")
        return

    if not config.get("llm", {}).get("api_key"):
        _log("api_key 未配置，/chat 和 /get-license 将不可用，请在配置中填写 [llm].api_key", "ERROR")

    # ── 主循环保活 ──
    while True:
        time.sleep(60)


def on_config_reload(new_config):
    """配置热重载回调：整体替换共享配置引用，api_key/model 等热生效"""
    _state["config"] = new_config
    logger.info("配置已重新加载")


def on_shutdown():
    """服务关闭回调"""
    logger.info("LLM API 代理服务正在关闭")
