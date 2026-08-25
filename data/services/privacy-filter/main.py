"""Privacy Filter 脱敏服务

基于 OpenAI Privacy Filter (opf) 模型对输入文本进行 PII 检测与脱敏处理，
通过 web-service 模块暴露 HTTP API。

API（通过 web-service 模块提供，URL 自动添加服务名前缀）：
- GET  /        → 脱敏测试页面（静态 HTML，平台 Web 按钮默认打开）
- GET  /health  → 健康检查（模型加载状态、设备、checkpoint 路径）
- POST /redact  → 文本脱敏（text 必填，output_mode 可选 typed|redacted）

依赖模块：log-enhancer, web-service
依赖包：torch, safetensors, tiktoken, huggingface_hub, numpy, packaging
"""
import json
import logging
import platform
import shutil
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# 共享状态：config 引用 + 懒加载 OPF 实例 + 线程锁 + 启动环境检查结果
_state = {"config": {}, "opf": None, "lock": threading.Lock(), "env": {}}


# 脱敏测试页面（GET /privacy-filter/，平台 Web 按钮默认打开）
_TEST_PAGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Privacy Filter 脱敏测试</title>
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
        font-family: -apple-system, "PingFang SC", "Helvetica Neue", sans-serif;
        background: #f5f6f8; color: #24292f;
        display: flex; flex-direction: column; min-height: 100vh;
    }
    .container { max-width: 760px; width: 100%; margin: 0 auto; padding: 24px 16px; flex: 1; }
    h1 { font-size: 20px; margin-bottom: 4px; }
    .sub { font-size: 13px; color: #57606a; margin-bottom: 20px; }
    textarea {
        width: 100%; height: 140px; padding: 12px;
        border: 1px solid #d0d7de; border-radius: 6px;
        font-size: 14px; font-family: inherit; resize: vertical;
    }
    textarea:focus { outline: none; border-color: #0969da; box-shadow: 0 0 0 3px rgba(9,105,218,.15); }
    .toolbar { display: flex; align-items: center; gap: 12px; margin: 12px 0; }
    .toolbar label { font-size: 13px; color: #57606a; display: flex; align-items: center; gap: 4px; }
    button {
        margin-left: auto; padding: 8px 20px;
        background: #0969da; color: #fff; border: none; border-radius: 6px;
        font-size: 14px; cursor: pointer;
    }
    button:hover { background: #0860c4; }
    button:disabled { background: #a0c7f0; cursor: not-allowed; }
    .result { display: none; }
    .result h2 { font-size: 14px; color: #57606a; margin-bottom: 8px; }
    .output {
        background: #fff; border: 1px solid #d0d7de; border-radius: 6px;
        padding: 12px; font-size: 14px; white-space: pre-wrap; word-break: break-word;
        min-height: 48px;
    }
    .badges { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
    .badge {
        font-size: 12px; background: #ddf4ff; color: #0969da;
        border-radius: 10px; padding: 2px 10px;
    }
    .error-msg {
        display: none; background: #fff1f0; border: 1px solid #ffccc7;
        color: #cf1322; border-radius: 6px; padding: 10px 12px;
        font-size: 13px; margin-top: 12px; word-break: break-all;
    }
    footer {
        text-align: center; font-size: 12px; color: #8b949e;
        padding: 10px 0 16px;
    }
</style>
</head>
<body>
<div class="container">
    <h1>Privacy Filter 脱敏测试</h1>
    <p class="sub">输入文本，检测并脱敏其中的隐私信息（人名、邮箱、电话、地址、日期、密钥等）</p>
    <textarea id="input" placeholder="输入待脱敏文本，例如：Alice was born on 1990-01-02. Contact: alice@example.com, 13800138000."></textarea>
    <div class="toolbar">
        <label><input type="checkbox" id="redacted-mode"> redacted 模式（统一占位符）</label>
        <button id="btn" onclick="doRedact()">脱敏处理</button>
    </div>
    <div class="error-msg" id="error"></div>
    <div class="result" id="result">
        <h2>脱敏结果</h2>
        <div class="output" id="output"></div>
        <div class="badges" id="badges"></div>
    </div>
</div>
<footer id="footer">输入 0 字 · 输出 0 字 · 耗时 0ms</footer>
<script>
const base = location.pathname.endsWith('/') ? location.pathname : location.pathname + '/';  // /privacy-filter 或 /privacy-filter/ → /privacy-filter/
const $ = id => document.getElementById(id);

async function doRedact() {
    const text = $('input').value.trim();
    const errorBox = $('error');
    errorBox.style.display = 'none';
    if (!text) {
        errorBox.textContent = '请输入待脱敏文本';
        errorBox.style.display = 'block';
        return;
    }
    const payload = { text: text };
    if ($('redacted-mode').checked) payload.output_mode = 'redacted';

    const btn = $('btn');
    btn.disabled = true;
    btn.textContent = '处理中...';
    const t0 = performance.now();
    try {
        const resp = await fetch(base + 'redact', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const elapsed = Math.round(performance.now() - t0);
        const body = await resp.json();
        if (!resp.ok || body.success === false) {
            const msg = body.error || body.detail || ('HTTP ' + resp.status);
            errorBox.textContent = '脱敏失败: ' + msg;
            errorBox.style.display = 'block';
            return;
        }
        renderResult(body.data, elapsed, body.request_id);
    } catch (e) {
        errorBox.textContent = '请求失败: ' + e;
        errorBox.style.display = 'block';
    } finally {
        btn.disabled = false;
        btn.textContent = '脱敏处理';
    }
}

function renderResult(data, elapsed, requestId) {
    $('output').textContent = data.redacted_text;
    const badges = $('badges');
    badges.innerHTML = '';
    const byLabel = data.by_label || {};
    for (const [label, count] of Object.entries(byLabel)) {
        const span = document.createElement('span');
        span.className = 'badge';
        span.textContent = label + ' × ' + count;
        badges.appendChild(span);
    }
    $('result').style.display = 'block';
    const footer = $('footer');
    footer.textContent = '输入 ' + data.text.length + ' 字 · 输出 '
        + data.redacted_text.length + ' 字 · 耗时 ' + elapsed + 'ms'
        + (requestId ? ' · request_id: ' + requestId : '');
}

$('input').addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') doRedact();
});
</script>
</body>
</html>
"""


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
                    "device": _detect_device(cfg),
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


def _detect_device(cfg):
    """解析推理设备。

    config [model].device 为 "cpu"/"cuda" 时直接采用；
    为 "auto"（默认）时按平台自动选择：Linux 且 CUDA 可用 → cuda，否则 cpu。
    macOS 无 CUDA，自动回退 cpu。
    """
    device = cfg.get("device", "auto")
    if device in ("cpu", "cuda"):
        return device
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def _check_environment(log_fn):
    """启动时环境检查（跨平台，含 Linux）。

    检查操作系统、Python 版本、opf 源码路径、torch/CUDA、推理设备、
    checkpoint、gitleaks 二进制，记录日志并缓存到 _state["env"] 供 /health 暴露。
    """
    env = {
        "system": platform.system(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
    }
    log_fn(f"[ENV] 操作系统: {env['system']} {env['machine']} | Python {env['python']}")

    model_cfg = _state["config"].get("model", {})

    # opf 源码路径
    source_path = model_cfg.get("source_path", "")
    env["opf_source_ok"] = bool(source_path and Path(source_path).exists())
    log_fn(f"[ENV] opf 源码路径: {source_path or '(未配置)'} -> {'OK' if env['opf_source_ok'] else '缺失'}",
           "INFO" if env["opf_source_ok"] else "WARNING")

    # torch 与 CUDA（Linux 下 CUDA 决定是否启用 GPU 推理）
    torch_ok, cuda_ok = False, False
    try:
        import torch
        torch_ok = True
        cuda_ok = bool(torch.cuda.is_available())
    except Exception as e:
        log_fn(f"[ENV] torch 不可用: {e}", "ERROR")
    env["torch_ok"] = torch_ok
    env["cuda_ok"] = cuda_ok
    log_fn(f"[ENV] torch: {'OK' if torch_ok else '缺失'} | CUDA: {'可用' if cuda_ok else '不可用'}")

    # 推理设备
    device = _detect_device(model_cfg)
    env["device"] = device
    if model_cfg.get("device") == "cuda" and not cuda_ok:
        log_fn("[ENV] 配置 device=cuda 但 CUDA 不可用，实际将回退 cpu", "WARNING")
    log_fn(f"[ENV] 推理设备: {device}")

    # checkpoint
    ckpt = model_cfg.get("checkpoint") or "~/.opf/privacy_filter"
    env["checkpoint_ok"] = Path(ckpt).expanduser().exists()
    log_fn(f"[ENV] checkpoint: {ckpt} -> {'OK' if env['checkpoint_ok'] else '未下载(首次将自动下载)'}")

    # gitleaks 二进制（可选引擎）
    gl_cfg = _state["config"].get("gitleaks", {})
    gl_bin = gl_cfg.get("binary", "gitleaks")
    gl_path = shutil.which(gl_bin)
    env["gitleaks_ok"] = bool(gl_path)
    env["gitleaks_path"] = gl_path or ""
    log_fn(f"[ENV] gitleaks: {gl_path or '未安装(可选)'}",
           "INFO" if gl_path else "WARNING")

    _state["env"] = env
    return env


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

    # ── 启动环境检查（跨平台，含 Linux/CUDA） ──
    _check_environment(_log)

    # ── Web 路由处理器 ──

    def handle_health():
        """GET /health — 健康检查（register_api 无参回调，返回 dict 自动包装）"""
        model_cfg = _state["config"].get("model", {})
        env = _state.get("env", {})
        return {
            "status": "ok",
            "model_loaded": _state["opf"] is not None,
            "device": env.get("device", model_cfg.get("device", "cpu")),
            "output_mode": model_cfg.get("output_mode", "typed"),
            "checkpoint": model_cfg.get("checkpoint", "~/.opf/privacy_filter"),
            "environment": env,
        }

    def handle_redact(request_info):
        """POST /redact — 文本脱敏处理"""
        request_id = _new_request_id()
        _log(f"[REQUEST_START] ID={request_id} path=/redact")
        try:
            data, err_resp = _parse_body(request_info)
            if err_resp:
                err_body = json.loads(err_resp["body"])
                err_body["request_id"] = request_id
                err_resp["body"] = json.dumps(err_body, ensure_ascii=False)
                return err_resp

            text = data.get("text")
            if not isinstance(text, str) or not text.strip():
                _log(f"[REQUEST_VALIDATION] ID={request_id} Error: text 参数必须是非空字符串", "WARNING")
                return _json_response(400, {
                    "success": False, "data": None, "error": "text 参数必须是非空字符串",
                    "request_id": request_id,
                })

            opf = _state["opf"]
            if opf is None:
                return _json_response(503, {
                    "success": False, "data": None,
                    "error": "模型未加载，请稍后重试或检查服务日志",
                    "request_id": request_id,
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
                "data": {
                    "by_label": result_dict.get("summary", {}).get("by_label", {}),
                    "text": result_dict.get("text", ""),
                    "redacted_text": result_dict.get("redacted_text", ""),
                },
                "error": None,
                "request_id": request_id,
            })

        except RuntimeError as e:
            _log(f"[MODEL_ERROR] ID={request_id} Error={e}", "ERROR")
            return _json_response(503, {
                "success": False, "data": None,
                "error": f"模型加载失败: {e}",
                "request_id": request_id,
            })
        except ValueError as ve:
            _log(f"[CONFIG_ERROR] ID={request_id} Error={ve}", "ERROR")
            return _json_response(503, {
                "success": False, "data": None, "error": str(ve),
                "request_id": request_id,
            })
        except Exception as e:
            _log(f"[UNEXPECTED_ERROR] ID={request_id} ErrorType={type(e).__name__} ErrorMsg={e}", "ERROR")
            return _json_response(500, {
                "success": False, "data": None, "error": f"服务内部错误: {e}",
                "request_id": request_id,
            })
        finally:
            _log(f"[REQUEST_END] ID={request_id}")

    # ── 注册 Web 路由（通过 web-service 模块） ──
    web_mod = modules.get("web-service")
    if web_mod:
        web_mod.register_page("/", _TEST_PAGE_HTML)
        web_mod.register_api("/health", handle_health)
        web_mod.register_handler("/redact", "POST", handle_redact)

        port_info = web_mod.get_port()
        if port_info.get("success"):
            _log(f"Privacy Filter 脱敏服务已启动，测试页面: {port_info['data']['base_url']}")
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
