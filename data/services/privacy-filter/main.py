"""Privacy Filter 脱敏服务

基于 OpenAI Privacy Filter (opf) 模型对输入文本进行 PII 检测与脱敏处理，
通过 web-service 模块暴露 HTTP API。

API（通过 web-service 模块提供，URL 自动添加服务名前缀）：
- GET  /        → 健康检查（模型加载状态、设备、checkpoint 路径）
- POST /redact  → 文本脱敏（text 必填，output_mode 可选 typed|redacted）

依赖模块：log-enhancer, web-service
依赖包：torch, safetensors, tiktoken, huggingface_hub, numpy, packaging
"""
import json
import logging
import sys
import threading
import time

logger = logging.getLogger(__name__)

# 共享状态：config 引用 + 懒加载 OPF 实例 + 线程锁
_state = {"config": {}, "opf": None, "lock": threading.Lock()}


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


def _get_opf():
    """懒加载 OPF 实例（线程安全，双重检查锁）。

    首次调用时根据 config.toml [model] 配置加载模型，后续复用缓存实例。
    模型加载约 10-30 秒（~2.8GB safetensors），仅首次请求耗时。

    Returns:
        OPF 实例

    Raises:
        RuntimeError: 模型 checkpoint 加载失败
        ValueError: 配置无效
    """
    if _state["opf"] is None:
        with _state["lock"]:
            if _state["opf"] is None:
                cfg = _state["config"].get("model", {})
                source_path = cfg.get("source_path", "")
                if source_path and source_path not in sys.path:
                    sys.path.insert(0, source_path)
                from opf import OPF

                opf_kwargs = {
                    "device": cfg.get("device", "cpu"),
                    "output_mode": cfg.get("output_mode", "typed"),
                }
                checkpoint = cfg.get("checkpoint")
                if checkpoint:
                    opf_kwargs["model"] = checkpoint
                _state["opf"] = OPF(**opf_kwargs)
    return _state["opf"]


def _collapse_to_redacted(result_dict):
    """将 typed 模式的结果后处理为 redacted 模式（避免重新加载模型）。

    把所有 span 标签统一为 "redacted"，占位符统一为 "<REDACTED>"，
    并重建 redacted_text。
    """
    spans = result_dict.get("detected_spans", [])
    text = result_dict.get("text", "")
    new_spans = []
    pieces = []
    cursor = 0
    for span in spans:
        pieces.append(text[cursor:span["start"]])
        pieces.append("<REDACTED>")
        cursor = span["end"]
        new_spans.append({
            "label": "redacted",
            "start": span["start"],
            "end": span["end"],
            "text": span["text"],
            "placeholder": "<REDACTED>",
        })
    pieces.append(text[cursor:])
    result_dict["detected_spans"] = new_spans
    result_dict["redacted_text"] = "".join(pieces)
    result_dict["summary"]["output_mode"] = "redacted"
    result_dict["summary"]["by_label"] = {"redacted": len(new_spans)}
    return result_dict


def _preload_model(log_fn):
    """预加载模型权重到内存（启动时调用，避免首次请求超时）。

    OPF 构造函数仅保存配置，实际权重在 get_runtime() 时加载。
    通过显式调用 get_runtime() 强制加载 ~2.8GB safetensors。
    """
    opf = _get_opf()
    opf.get_runtime()
    log_fn("模型权重已加载到内存")


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
        model_cfg = _state["config"].get("model", {})
        return {
            "status": "ok",
            "model_loaded": _state["opf"] is not None,
            "device": model_cfg.get("device", "cpu"),
            "output_mode": model_cfg.get("output_mode", "typed"),
            "checkpoint": model_cfg.get("checkpoint", "~/.opf/privacy_filter"),
        }

    def handle_redact(request_info):
        """POST /redact — 文本脱敏处理"""
        request_id = _new_request_id()
        _log(f"[REQUEST_START] ID={request_id} path=/redact")
        try:
            data, err_resp = _parse_body(request_info)
            if err_resp:
                return err_resp

            text = data.get("text")
            if not isinstance(text, str) or not text.strip():
                _log(f"[REQUEST_VALIDATION] ID={request_id} Error: text 参数必须是非空字符串", "WARNING")
                return _json_response(400, {
                    "success": False, "data": None, "error": "text 参数必须是非空字符串"
                })

            opf = _state["opf"]
            if opf is None:
                return _json_response(503, {
                    "success": False, "data": None,
                    "error": "模型未加载，请稍后重试或检查服务日志",
                })

            _log(f"[REDACT_START] ID={request_id} text_len={len(text)}")
            result = opf.redact(text)
            result_dict = result.to_dict()

            # 请求级 output_mode=redacted 时，后处理折叠标签
            # （避免调用 set_output_mode 导致模型重新加载）
            if data.get("output_mode") == "redacted":
                result_dict = _collapse_to_redacted(result_dict)

            span_count = result_dict.get("summary", {}).get("span_count", 0)
            _log(f"[REDACT_SUCCESS] ID={request_id} spans={span_count}")
            return _json_response(200, {
                "success": True,
                "data": result_dict,
                "error": None,
            })

        except RuntimeError as e:
            _log(f"[MODEL_ERROR] ID={request_id} Error={e}", "ERROR")
            return _json_response(503, {
                "success": False, "data": None,
                "error": f"模型加载失败: {e}",
            })
        except ValueError as ve:
            _log(f"[CONFIG_ERROR] ID={request_id} Error={ve}", "ERROR")
            return _json_response(503, {
                "success": False, "data": None, "error": str(ve),
            })
        except Exception as e:
            _log(f"[UNEXPECTED_ERROR] ID={request_id} ErrorType={type(e).__name__} ErrorMsg={e}", "ERROR")
            return _json_response(500, {
                "success": False, "data": None, "error": f"服务内部错误: {e}",
            })
        finally:
            _log(f"[REQUEST_END] ID={request_id}")

    # ── 注册 Web 路由（通过 web-service 模块） ──
    web_mod = modules.get("web-service")
    if web_mod:
        web_mod.register_api("/", handle_health)
        web_mod.register_handler("/redact", "POST", handle_redact)

        port_info = web_mod.get_port()
        if port_info.get("success"):
            _log(f"Privacy Filter 脱敏服务已启动: {port_info['data']['base_url']}")
    else:
        _log("web-service 模块未启用，HTTP 接口不可用", "ERROR")
        return

    # ── 预加载模型（启动时加载，避免首次请求超时） ──
    try:
        _log("开始加载 OPF 模型（约 10-30 秒）...")
        _preload_model(_log)
        _log("OPF 模型加载完成，服务就绪")
    except Exception as e:
        _log(f"OPF 模型加载失败: {e}", "ERROR")
        _log("服务继续运行，/redact 将返回 503", "WARNING")

    # ── 主循环保活 ──
    while True:
        time.sleep(60)


def on_config_reload(new_config):
    """配置热重载回调：整体替换共享配置引用。

    注意：若 model 配置变更（device/output_mode/checkpoint），
    需重启服务才能生效（OPF 实例已缓存旧配置）。
    """
    _state["config"] = new_config
    logger.info("配置已重新加载（模型配置变更需重启服务生效）")


def on_shutdown():
    """服务关闭回调"""
    logger.info("Privacy Filter 脱敏服务正在关闭")


def _new_request_id():
    """生成毫秒级请求 ID。"""
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
