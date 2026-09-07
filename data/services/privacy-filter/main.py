"""Privacy Filter 脱敏服务（移植自 cn_pii_anonymization）

双管线：
- 文本：AIguard（Qwen3 token-classification NER，21 类中文 PII，<label> 占位符脱敏）
- 图像：PaddleOCR + Presidio 正则识别器 + 马赛克/高斯模糊/纯色填充

库代码经 config [model].source_path 注入 sys.path 加载，不做 pip 安装。
源项目的 AIGUARD_*/OCR_* 配置项通过环境变量桥接（settings 为 pydantic-settings）。

API（web-service 模块提供，URL 自动加 /privacy-filter 前缀）：
- GET  /                脱敏测试页
- GET  /health          健康检查（引擎状态 + 环境 + 缓存统计）
- POST /text/anonymize  文本脱敏（AIguard）
- POST /text/analyze    文本仅识别
- POST /image/anonymize 图像脱敏（image_base64 进、PNG base64 出）
- POST /image/analyze   图像仅识别

依赖模块：log-enhancer, web-service
"""
import base64
import binascii
import hashlib
import importlib.metadata
import io
import json
import logging
import os
import platform
import sys
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

MOSAIC_STYLES = ("pixel", "blur", "fill")

_state = {
    "config": {},
    "env": {},
    "aiguard": None,
    "aiguard_error": None,
    "image_processor": None,
    "redactor": None,
    "image_error": None,
    "image_ready": False,
    "init_lock": threading.Lock(),
    "text_lock": threading.Lock(),
    "image_lock": threading.Lock(),
    "started_at": time.time(),
}


def _cfg(section, key, default=None):
    """读取 config.toml 指定段的配置项。"""
    return _state["config"].get(section, {}).get(key, default)


def _apply_env_overrides(config):
    """把 config.toml 的模型/缓存/设备配置桥接为环境变量。

    必须在首次 import cn_pii_anonymization 之前调用：源项目 settings 是
    pydantic-settings 实例，在模块导入时即完成实例化，之后改环境变量无效。
    """
    for k, v in {"FLAGS_use_mkldnn": "0", "FLAGS_enable_pir_api": "0",
                 "FLAGS_json_format_model": "0", "PADDLE_PDX_MODEL_SOURCE": "bos",
                 "FLAGS_enable_onednn_backend": "0", "FLAGS_disable_onednn_backend": "1",
                 "PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT": "0",
                 "MPLCONFIGDIR": str(Path(tempfile.gettempdir()) / "pyservice-privacy-filter-mpl")}.items():
        os.environ.setdefault(k, v)

    bool_to_env = lambda val: "true" if val else "false"

    aig = config.get("aiguard", {})
    for key, env_name in (("device", "AIGUARD_DEVICE"), ("model_name", "AIGUARD_MODEL_NAME"),
                          ("chunk_chars", "AIGUARD_MAX_CHARS"), ("cache_size", "AIGUARD_CACHE_SIZE")):
        if aig.get(key) is not None:
            os.environ[env_name] = str(aig[key])

    img = config.get("image", {})
    for key, env_name, conv in (
        ("ocr_use_gpu", "OCR_USE_GPU", bool_to_env),
        ("ocr_model_dir", "OCR_MODEL_DIR", str),
        ("ocr_cache_size", "OCR_CACHE_SIZE", str),
        ("ocr_language", "OCR_LANGUAGE", str),
        ("max_image_bytes", "MAX_IMAGE_SIZE", str),
    ):
        if img.get(key) is not None:
            os.environ[env_name] = conv(img[key])

    for key, value in (config.get("env") or {}).items():
        os.environ[key.upper()] = str(Path(str(value)).expanduser())


def _configure_source_logging(config):
    """按 [logging].level 重装 loguru handler。

    源模块使用 loguru 全局 logger，其默认 handler 为 DEBUG，会把整份 OCR 结果
    （rec_texts/rec_scores/rec_polys 等大字典）写进 runner.log。
    """
    level = str(config.get("logging", {}).get("level", "INFO")).upper()
    from loguru import logger as source_logger

    source_logger.remove()
    source_logger.add(
        sys.stderr,
        level=level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    )
    return level


def _ensure_source_path():
    """把源项目 src 目录插入 sys.path。"""
    source_path = _cfg("model", "source_path", "")
    if not source_path:
        raise ValueError("config.toml [model].source_path 未配置")
    if source_path not in sys.path:
        sys.path.insert(0, source_path)
    return source_path


def _get_aiguard():
    """获取 AIguard 引擎（线程安全双重检查懒加载）。

    引擎自身懒加载模型权重（首次约 10-30 秒，含 ~2.4GB 模型下载）。
    """
    if _state["aiguard"] is None:
        with _state["init_lock"]:
            if _state["aiguard"] is None:
                _ensure_source_path()
                from cn_pii_anonymization.nlp.aiguard_engine import get_aiguard_engine
                _state["aiguard"] = get_aiguard_engine()
    return _state["aiguard"]


def _get_image_pipeline():
    """获取图像处理器与脱敏引擎（线程安全双重检查懒加载）。

    构造 ImageProcessor 会同步初始化 CNPIIAnalyzerEngine（LAC 分词 + 识别器注册），
    首次耗时较长。redactor 单独持有以便读取 OCR 结果（get_ocr_result 为公开方法）。
    """
    if _state["image_processor"] is None:
        with _state["init_lock"]:
            if _state["image_processor"] is None:
                _ensure_source_path()
                from cn_pii_anonymization.core.image_redactor import (
                    CNPIIImageRedactorEngine,
                )
                from cn_pii_anonymization.processors.image_processor import (
                    ImageProcessor,
                )

                redactor = CNPIIImageRedactorEngine()
                _state["redactor"] = redactor
                _state["image_processor"] = ImageProcessor(redactor=redactor)
    return _state["image_processor"]


def _detect_serialized(text):
    """串行执行 AIguard 推理。

    引擎内部锁只覆盖懒加载与缓存写入，detect() 的推理与 LRU move_to_end 无锁，
    而回调服务器是 ThreadingHTTPServer，故在服务层整体串行化。
    """
    engine = _get_aiguard()
    with _state["text_lock"]:
        return engine, engine.detect(text)


def _build_anonymized(text, entities):
    """按 start 升序用 <label> 占位符重建文本，跳过重叠实体。"""
    parts = []
    cursor = 0
    used = []
    by_label = {}
    for ent in sorted(entities, key=lambda e: e["start"]):
        if ent["start"] < cursor:
            continue
        if ent["start"] > cursor:
            parts.append(text[cursor:ent["start"]])
        parts.append(f"<{ent['label']}>")
        used.append(ent)
        by_label[ent["label"]] = by_label.get(ent["label"], 0) + 1
        cursor = ent["end"]
    if cursor < len(text):
        parts.append(text[cursor:])
    return "".join(parts), used, by_label


def _decode_image(data):
    """解析并校验 image_base64，返回 (PIL图像, bytes, 错误响应)。"""
    raw = data.get("image_base64")
    if not isinstance(raw, str) or not raw.strip():
        return None, None, "image_base64 参数必须是非空字符串"
    if "," in raw[:64] and raw.strip().lower().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        image_bytes = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as e:
        return None, None, f"image_base64 解码失败: {e}"

    max_bytes = int(_cfg("image", "max_image_bytes", 10 * 1024 * 1024))
    if len(image_bytes) > max_bytes:
        return None, None, f"图像大小 {len(image_bytes)} 字节超过上限 {max_bytes} 字节"

    from PIL import Image
    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
        if image.mode != "RGB":
            image = image.convert("RGB")
    except Exception as e:
        return None, None, f"图像无法识别: {e}"
    return image, image_bytes, None


def _parse_fill_color(value):
    """解析 "R,G,B" 或 [R,G,B] 填充色，非法时抛 ValueError。"""
    if value is None:
        return (0, 0, 0)
    if isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        parts = str(value).replace("，", ",").split(",")
    rgb = tuple(int(str(p).strip()) for p in parts)
    if len(rgb) != 3 or not all(0 <= c <= 255 for c in rgb):
        raise ValueError("fill_color 必须是三个 0-255 的整数，如 \"0,0,0\"")
    return rgb


def _ocr_payload():
    """读取最近一次 OCR 结果（图像请求已在 image_lock 内串行，取值安全）。"""
    redactor = _state.get("redactor")
    ocr = redactor.get_ocr_result() if redactor else None
    if not ocr:
        return "", 0.0
    return ocr.text or "", getattr(ocr, "confidence", 0.0) or 0.0


def _entity_counts(entities, key):
    counts = {}
    for ent in entities:
        label = ent[key]
        counts[label] = counts.get(label, 0) + 1
    return counts


def _check_environment(log_fn):
    """启动环境检查：版本、设备、模型缓存，结果缓存供 /health 暴露。

    只用 importlib.metadata 读包元数据，不 import paddle（导入代价高）。
    """
    env = {
        "system": platform.system(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
    }
    log_fn(f"[ENV] 操作系统: {env['system']} {env['machine']} | Python {env['python']}")
    if sys.version_info < (3, 12):
        log_fn("[ENV] 源项目声明 requires-python>=3.12，当前 3.11（已验证无 3.12 专属语法）")

    source_path = _cfg("model", "source_path", "")
    pkg_dir = Path(source_path) / "cn_pii_anonymization" if source_path else Path("")
    env["source_ok"] = pkg_dir.is_dir()
    log_fn(f"[ENV] cn_pii_anonymization 源码: {source_path or '(未配置)'} -> "
           f"{'OK' if env['source_ok'] else '缺失'}",
           "INFO" if env["source_ok"] else "ERROR")

    versions = {}
    for pkg in ("presidio-analyzer", "presidio-anonymizer", "paddlenlp", "paddleocr",
                "paddlepaddle", "transformers", "tokenizers", "pillow", "faker"):
        try:
            versions[pkg] = importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            versions[pkg] = None
            log_fn(f"[ENV] 依赖缺失: {pkg}", "ERROR")
    env["versions"] = versions
    log_fn("[ENV] 版本 " + " | ".join(f"{k}={v}" for k, v in versions.items() if v))

    tok_ver = versions.get("tokenizers") or "0"
    env["tokenizers_ok"] = int(tok_ver.split(".")[1] or 0) >= 22
    if not env["tokenizers_ok"]:
        log_fn(f"[ENV] tokenizers {tok_ver} < 0.22，将被 paddlenlp 降级导致 AIguard 崩溃，"
               "需重跑依赖修复安装", "ERROR")

    torch_ok, cuda_ok, mps_ok = False, False, False
    try:
        import torch
        torch_ok = True
        cuda_ok = bool(torch.cuda.is_available())
        mps_ok = bool(getattr(torch, "backends", None) and torch.backends.mps.is_available())
    except Exception as e:
        log_fn(f"[ENV] torch 不可用: {e}", "ERROR")
    env.update({"torch_ok": torch_ok, "cuda_ok": cuda_ok, "mps_ok": mps_ok})
    env["aiguard_device"] = _resolve_aiguard_device(cuda_ok, mps_ok)
    log_fn(f"[ENV] torch: {'OK' if torch_ok else '缺失'} | CUDA: {cuda_ok} | MPS: {mps_ok} "
           f"| AIguard 设备: {env['aiguard_device']}")

    env["aiguard_model_cached"] = _hf_model_cached(_cfg("aiguard", "model_name",
                                                        "ZJUICSR/AIguard-pii-detection-fast"))
    log_fn(f"[ENV] AIguard 模型缓存: {'已就位' if env['aiguard_model_cached'] else '未缓存(首次请求将自动下载 ~2.4GB)'}")
    if env["aiguard_model_cached"]:
        # 必须在 transformers/huggingface_hub 首次 import 前设置：二者在导入时读取该变量
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
    log_fn(f"[ENV] HF_HUB_OFFLINE={os.environ.get('HF_HUB_OFFLINE', 'unset')}"
           "（缓存命中时跳过 huggingface.co 版本校验，避免离线环境重试超时）")

    paddle_home = Path("~/.paddlex").expanduser()
    env["paddle_model_cached"] = paddle_home.exists()
    log_fn(f"[ENV] Paddle 模型缓存: {paddle_home} -> "
           f"{'已就位' if env['paddle_model_cached'] else '未缓存(首次将自动下载)'}")

    _state["env"] = env
    return env


def _resolve_aiguard_device(cuda_ok, mps_ok):
    """解析 AIguard 实际推理设备（auto 时按 cuda > mps > cpu）。"""
    pref = str(_cfg("aiguard", "device", "auto")).lower()
    if pref in ("cpu", "cuda", "mps"):
        return pref
    if cuda_ok:
        return "cuda"
    if mps_ok:
        return "mps"
    return "cpu"


def _hf_model_cached(model_name):
    """探测 HuggingFace 模型是否已缓存（仅 stat，不触发下载）。"""
    org, _, repo = model_name.partition("/")
    if not repo:
        return False
    hub = Path(os.environ.get("HF_HOME", Path("~/.cache/huggingface/hub").expanduser()))
    if not hub.is_absolute():
        hub = Path("~/.cache/huggingface/hub").expanduser()
    return (hub / f"models--{org}--{repo}").is_dir()


def _preload(log_fn):
    """启动预加载：触发模型下载与初始化，避免首次请求超时。

    失败不退出服务，对应端点返回 503。
    """
    if _cfg("aiguard", "enabled", True) and _cfg("preload", "aiguard", True):
        t0 = time.time()
        try:
            log_fn("开始预加载 AIguard 模型（首次含 ~2.4GB 下载）...")
            engine, ents = _detect_serialized("张三的手机号是13812345678")
            if engine.is_loaded():
                log_fn(f"AIguard 加载完成 device={engine._device} "
                       f"耗时 {time.time() - t0:.1f}s 探测实体 {len(ents)} 个")
            else:
                _state["aiguard_error"] = engine.get_init_error() or "模型未加载"
                log_fn(f"AIguard 未就绪: {_state['aiguard_error']}", "ERROR")
        except Exception as e:
            _state["aiguard_error"] = str(e)
            log_fn(f"AIguard 预加载失败: {e}", "ERROR")

    if _cfg("image", "enabled", True) and _cfg("preload", "image", True):
        t0 = time.time()
        try:
            log_fn("开始预加载图像管线（OCR/LAC/UIE 模型）...")
            processor = _get_image_pipeline()
            from PIL import Image
            probe = Image.new("RGB", (320, 80), "white")
            try:
                from PIL import ImageDraw
                ImageDraw.Draw(probe).text((12, 30), "Tel 13812345678", fill="black")
            except Exception:
                pass
            entities = processor.analyze_only(probe, ocr_cache_key="warmup")
            _state["image_ready"] = True
            log_fn(f"图像管线就绪，预热耗时 {time.time() - t0:.1f}s（探测实体 {len(entities)} 个）")
        except Exception as e:
            _state["image_error"] = str(e)
            log_fn(f"图像管线预加载失败: {e}（OCR 状态探测结果会粘滞，修复后需重启服务）", "ERROR")


def _json_response(status_code, body):
    """构造 JSON 响应。"""
    return {
        "status_code": status_code,
        "content_type": "application/json; charset=utf-8",
        "body": json.dumps(body, ensure_ascii=False),
    }


def _parse_body(request_info):
    """解析请求体，返回 (data, 错误响应)。"""
    try:
        data = json.loads(request_info.get("body") or "{}")
    except json.JSONDecodeError as e:
        return None, _json_response(400, {"success": False, "data": None,
                                          "error": f"invalid JSON: {e}"})
    if not isinstance(data, dict):
        return None, _json_response(400, {"success": False, "data": None,
                                          "error": "请求体必须是 JSON 对象"})
    return data, None


def _error(status_code, message, request_id):
    """统一错误响应。"""
    return _json_response(status_code, {"success": False, "data": None,
                                        "error": message, "request_id": request_id})


# 脱敏测试页面（GET /privacy-filter/，平台 Web 按钮默认打开）
_TEST_PAGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Privacy Filter 中文 PII 脱敏</title>
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
        font-family: -apple-system, "PingFang SC", "Helvetica Neue", sans-serif;
        background: #f5f6f8; color: #24292f;
        display: flex; flex-direction: column; min-height: 100vh;
    }
    .container { max-width: 900px; width: 100%; margin: 0 auto; padding: 24px 16px; flex: 1; }
    h1 { font-size: 20px; margin-bottom: 4px; }
    .sub { font-size: 13px; color: #57606a; margin-bottom: 16px; }
    .tabs { display: flex; gap: 8px; margin-bottom: 16px; border-bottom: 1px solid #d0d7de; }
    .tab {
        padding: 8px 16px; font-size: 14px; cursor: pointer; border: none;
        background: none; color: #57606a; border-bottom: 2px solid transparent;
    }
    .tab.active { color: #0969da; border-bottom-color: #0969da; font-weight: 600; }
    textarea {
        width: 100%; height: 130px; padding: 12px;
        border: 1px solid #d0d7de; border-radius: 6px;
        font-size: 14px; font-family: inherit; resize: vertical;
    }
    textarea:focus { outline: none; border-color: #0969da; box-shadow: 0 0 0 3px rgba(9,105,218,.15); }
    .toolbar { display: flex; align-items: center; gap: 10px; margin: 12px 0; flex-wrap: wrap; }
    .toolbar label { font-size: 13px; color: #57606a; display: flex; align-items: center; gap: 5px; }
    select, input[type=color] { padding: 5px 8px; border: 1px solid #d0d7de; border-radius: 6px; font-size: 13px; }
    button {
        padding: 8px 18px; background: #0969da; color: #fff; border: none;
        border-radius: 6px; font-size: 14px; cursor: pointer;
    }
    button.ghost { background: #fff; color: #24292f; border: 1px solid #d0d7de; }
    button:hover { opacity: .92; }
    button:disabled { background: #a0c7f0; color: #fff; cursor: not-allowed; border-color: #a0c7f0; }
    .card { background: #fff; border: 1px solid #d0d7de; border-radius: 6px; padding: 12px; font-size: 14px; }
    h2 { font-size: 13px; color: #57606a; margin: 16px 0 8px; font-weight: 600; }
    .hl { line-height: 1.9; word-break: break-word; white-space: pre-wrap; }
    .ent { border-radius: 3px; padding: 1px 3px; }
    .output { white-space: pre-wrap; word-break: break-word; min-height: 40px; }
    .badges { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
    .badge { font-size: 12px; border-radius: 10px; padding: 2px 10px; color: #fff; }
    .error-msg {
        display: none; background: #fff1f0; border: 1px solid #ffccc7; color: #cf1322;
        border-radius: 6px; padding: 10px 12px; font-size: 13px; margin: 12px 0; word-break: break-all;
    }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #eaeef2; }
    th { color: #57606a; font-weight: 600; }
    .imgwrap { position: relative; background: #fff; border: 1px solid #d0d7de; border-radius: 6px; padding: 8px; text-align: center; }
    .imgwrap img { max-width: 100%; max-height: 460px; }
    details { margin-top: 10px; font-size: 13px; }
    summary { cursor: pointer; color: #57606a; }
    footer { text-align: center; font-size: 12px; color: #8b949e; padding: 10px 0 16px; }
    .hidden { display: none; }
</style>
</head>
<body>
<div class="container">
    <h1>Privacy Filter 中文 PII 脱敏</h1>
    <p class="sub">文本走 AIguard（Qwen3 NER，21 类实体，&lt;label&gt; 占位符）；图像走 PaddleOCR + 正则识别 + 马赛克</p>
    <div class="tabs">
        <button class="tab active" id="tab-text" onclick="switchMode('text')">文本脱敏</button>
        <button class="tab" id="tab-image" onclick="switchMode('image')">图像脱敏</button>
    </div>
    <div class="error-msg" id="error"></div>

    <div id="pane-text">
        <textarea id="input" placeholder="例如：张伟的手机号是13812345678，身份证号1101011990030701X，邮箱zhangwei@example.com">张伟的手机号是13812345678，身份证号1101011990030701X，邮箱zhangwei@example.com，家住北京市朝阳区望京街道。</textarea>
        <div class="toolbar">
            <button id="btn-anon" onclick="runText(true)">脱敏处理</button>
            <button class="ghost" id="btn-detect" onclick="runText(false)">仅识别</button>
            <span id="device-text" style="font-size:12px;color:#8b949e;margin-left:auto"></span>
        </div>
        <div id="text-result" class="hidden">
            <h2>原文高亮</h2>
            <div class="card hl" id="highlight"></div>
            <h2>脱敏结果</h2>
            <div class="card output" id="output"></div>
            <div class="badges" id="badges"></div>
        </div>
    </div>

    <div id="pane-image" class="hidden">
        <div class="toolbar">
            <input type="file" id="file" accept="image/png,image/jpeg,image/bmp,image/gif,image/webp">
            <label>样式
                <select id="style">
                    <option value="pixel">像素马赛克</option>
                    <option value="blur">高斯模糊</option>
                    <option value="fill">纯色填充</option>
                </select>
            </label>
            <label id="color-label">颜色 <input type="color" id="fillcolor" value="#000000"></label>
            <button id="btn-img" onclick="runImage(true)">图像脱敏</button>
            <button class="ghost" id="btn-img-detect" onclick="runImage(false)">仅识别</button>
        </div>
        <div id="img-result" class="hidden">
            <h2>结果图像 <button class="ghost" style="font-size:12px;padding:3px 10px;margin-left:8px" id="toggle-orig" onclick="toggleOrig()">查看原图</button></h2>
            <div class="imgwrap"><img id="result-img" alt="处理结果"></div>
            <h2>识别实体</h2>
            <div class="card"><table id="ent-table"></table></div>
            <details><summary>OCR 全文</summary><div class="card" id="ocr-text" style="margin-top:8px;white-space:pre-wrap"></div></details>
        </div>
    </div>
</div>
<footer id="footer">Privacy Filter · cn_pii_anonymization</footer>
<script>
const base = location.pathname.replace(/\\/+$/, '');  // /privacy-filter → 端点 /privacy-filter/xxx
const $ = id => document.getElementById(id);
let mode = 'text', originalUrl = '', resultUrl = '', lastImage = '';

const PALETTE = ['#0969da','#bf3989','#9a6700','#116329','#cf222e','#8250df','#1b7c83','#a05a00',
                 '#5e2a84','#0a6b4d','#8b0000','#3d4b62','#7d4b12','#005cc2','#6e40c9'];
const LABELS = {name:'姓名',id_card:'身份证',mobile:'手机',address:'地址',email:'邮箱',passport:'护照',
    hkmtp_pass:'港澳台通行证',social_security:'社保卡',drivers_license:'驾照',plate_number:'车牌',
    bank_card:'银行卡',credit_card:'信用卡',bank_password:'银行密码',birth_date:'出生日期',
    insurance_policy:'保单',taobao_order:'淘宝单',jd_order:'京东单',pdd_order:'拼多多单',
    ems_tracking:'EMS单',sf_tracking:'顺丰单',yto_tracking:'圆通单',
    CN_PHONE_NUMBER:'手机号',CN_ID_CARD:'身份证',CN_BANK_CARD:'银行卡',CN_PASSPORT:'护照',
    CN_EMAIL:'邮箱',CN_NAME:'姓名',CN_ADDRESS:'地址',CN_CVV:'安全码',CN_CARD_EXPIRY:'有效期'};
const colorOf = label => {
    let h = 0;
    for (const c of label) h = (h * 31 + c.charCodeAt(0)) >>> 0;
    return PALETTE[h % PALETTE.length];
};
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const show = (msg, box) => { box.style.display = 'block'; box.textContent = msg; };
const hide = box => box.style.display = 'none';

function switchMode(m) {
    mode = m;
    $('tab-text').classList.toggle('active', m === 'text');
    $('tab-image').classList.toggle('active', m === 'image');
    $('pane-text').classList.toggle('hidden', m !== 'text');
    $('pane-image').classList.toggle('hidden', m !== 'image');
    hide($('error'));
}

function badges(list, key) {
    const counts = {};
    list.forEach(e => counts[e[key]] = (counts[e[key]] || 0) + 1);
    const box = $('badges');
    box.innerHTML = '';
    Object.entries(counts).forEach(([label, n]) => {
        const span = document.createElement('span');
        span.className = 'badge';
        span.style.background = colorOf(label);
        span.textContent = (LABELS[label] || label) + ' × ' + n;
        box.appendChild(span);
    });
}

function highlight(text, ents) {
    let html = '', cursor = 0;
    for (const e of [...ents].sort((a, b) => a.start - b.start)) {
        if (e.start < cursor) continue;
        html += esc(text.slice(cursor, e.start));
        html += '<span class="ent" style="background:' + colorOf(e.label) + '33;color:'
              + colorOf(e.label) + '" title="' + esc(e.label) + ' (' + Math.round(e.score * 100) + '%)">'
              + esc(e.text) + '</span>';
        cursor = e.end;
    }
    return html + esc(text.slice(cursor));
}

async function post(path, payload, btns) {
    btns.forEach(b => { b.disabled = true; b.dataset.old = b.textContent; b.textContent = '处理中...'; });
    const t0 = performance.now();
    try {
        const resp = await fetch(base + path, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const elapsed = Math.round(performance.now() - t0);
        const body = await resp.json();
        if (!resp.ok || body.success === false) {
            show('失败: ' + (body.error || body.detail || ('HTTP ' + resp.status)), $('error'));
            return null;
        }
        return { data: body.data, elapsed, id: body.request_id };
    } catch (e) {
        show('请求失败: ' + e, $('error'));
        return null;
    } finally {
        btns.forEach(b => { b.disabled = false; b.textContent = b.dataset.old; });
    }
}

async function runText(anonymize) {
    hide($('error'));
    const text = $('input').value;
    if (!text.trim()) { show('请输入待处理文本', $('error')); return; }
    const r = await post(anonymize ? '/text/anonymize' : '/text/analyze',
                         { text }, [$('btn-anon'), $('btn-detect')]);
    if (!r) return;
    const d = r.data;
    $('highlight').innerHTML = highlight(d.original_text || text, d.pii_entities);
    $('output').textContent = anonymize ? d.anonymized_text : '（仅识别模式，未改写文本）';
    badges(d.pii_entities, 'label');
    $('text-result').classList.remove('hidden');
    $('device-text').textContent = 'device: ' + d.device;
    $('footer').textContent = (anonymize ? '输入 ' + d.original_text.length + ' 字 · 输出 ' + d.anonymized_text.length + ' 字' : '文本 ' + text.length + ' 字')
        + ' · 实体 ' + d.entity_count + ' · 耗时 ' + r.elapsed + 'ms · request_id: ' + r.id;
}

function readFile() {
    return new Promise((resolve, reject) => {
        const f = $('file').files[0];
        if (!f) { reject(new Error('请选择图片文件')); return; }
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(',', 2)[1]);
        reader.onerror = () => reject(new Error('文件读取失败'));
        reader.readAsDataURL(f);
    });
}

async function runImage(anonymize) {
    hide($('error'));
    let imageBase64;
    try { imageBase64 = await readFile(); } catch (e) { show(e.message, $('error')); return; }
    const payload = { image_base64: imageBase64 };
    if (anonymize) {
        payload.mosaic_style = $('style').value;
        if (payload.mosaic_style === 'fill') {
            const hex = $('fillcolor').value.slice(1);
            payload.fill_color = [0, 2, 4].map(i => parseInt(hex.slice(i, i + 2), 16)).join(',');
        }
    }
    const r = await post(anonymize ? '/image/anonymize' : '/image/analyze', payload,
                         [$('btn-img'), $('btn-img-detect')]);
    if (!r) return;
    const d = r.data;
    originalUrl = 'data:image/png;base64,' + imageBase64;
    resultUrl = anonymize ? 'data:image/png;base64,' + d.image_base64 : originalUrl;
    $('result-img').src = resultUrl;
    $('toggle-orig').style.display = anonymize ? '' : 'none';
    $('img-result').classList.remove('hidden');
    $('ent-table').innerHTML = '<tr><th>类型</th><th>文本</th><th>位置</th><th>置信度</th></tr>'
        + (d.pii_entities.map(e => '<tr><td style="color:' + colorOf(e.entity_type) + '">'
            + (LABELS[e.entity_type] || e.entity_type) + '</td><td>' + esc(e.text) + '</td><td>'
            + e.bbox.left + ',' + e.bbox.top + ' · ' + e.bbox.width + '×' + e.bbox.height
            + '</td><td>' + Math.round(e.score * 100) + '%</td></tr>').join('')
           || '<tr><td colspan="4">未识别到 PII</td></tr>');
    $('ocr-text').textContent = d.ocr_text || '（OCR 无文本）';
    $('footer').textContent = '图像 ' + d.image_size.width + '×' + d.image_size.height
        + ' · 实体 ' + d.entity_count + ' · OCR 置信度 ' + Math.round((d.ocr_confidence || 0) * 100) + '%'
        + ' · 耗时 ' + r.elapsed + 'ms · request_id: ' + r.id;
}

function toggleOrig() {
    const showingOrig = $('result-img').src === originalUrl;
    $('result-img').src = showingOrig ? resultUrl : originalUrl;
    $('toggle-orig').textContent = showingOrig ? '查看原图' : '查看结果图';
}

$('input').addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') runText(true);
});
$('style').addEventListener('change', () => {
    $('color-label').style.display = $('style').value === 'fill' ? '' : 'none';
});
$('color-label').style.display = 'none';
</script>
</body>
</html>
"""


def _handle_text(request_info, request_id, anonymize):
    """文本管线共用处理：anonymize=True 构造 <label> 脱敏文本。"""
    if not _cfg("aiguard", "enabled", True):
        return _error(503, "文本管线已禁用（config [aiguard].enabled=false）", request_id)

    data, err_resp = _parse_body(request_info)
    if err_resp:
        err_resp["body"] = json.dumps({**json.loads(err_resp["body"]), "request_id": request_id},
                                      ensure_ascii=False)
        return err_resp

    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        return _error(400, "text 参数必须是非空字符串", request_id)
    max_chars = int(_cfg("aiguard", "max_request_chars", 5000))
    if len(text) > max_chars:
        return _error(400, f"文本长度 {len(text)} 超过上限 {max_chars} 字符", request_id)

    engine, entities = _detect_serialized(text)
    if not engine.is_loaded():
        return _error(503, engine.get_init_error() or "AIguard 模型未加载", request_id)

    device = engine._device
    if anonymize:
        anonymized_text, used, by_label = _build_anonymized(text, entities)
        payload = {
            "original_text": text,
            "anonymized_text": anonymized_text,
            "pii_entities": [{**ent, "anonymized_text": f"<{ent['label']}>"} for ent in used],
            "by_label": by_label,
            "entity_count": len(used),
            "device": device,
        }
    else:
        payload = {
            "pii_entities": entities,
            "by_label": _entity_counts(entities, "label"),
            "entity_count": len(entities),
            "has_pii": bool(entities),
            "device": device,
        }
    return _json_response(200, {"success": True, "data": payload, "error": None,
                                "request_id": request_id})


def _handle_image(request_info, request_id, anonymize):
    """图像管线共用处理：anonymize=True 返回打码后 PNG base64。"""
    if not _cfg("image", "enabled", True):
        return _error(503, "图像管线已禁用（config [image].enabled=false）", request_id)

    data, err_resp = _parse_body(request_info)
    if err_resp:
        err_resp["body"] = json.dumps({**json.loads(err_resp["body"]), "request_id": request_id},
                                      ensure_ascii=False)
        return err_resp

    image, image_bytes, bad = _decode_image(data)
    if bad:
        return _error(400, bad, request_id)

    style = data.get("mosaic_style", "pixel")
    if anonymize and style not in MOSAIC_STYLES:
        return _error(400, f"mosaic_style 必须是 {'/'.join(MOSAIC_STYLES)} 之一", request_id)
    try:
        fill_color = _parse_fill_color(data.get("fill_color"))
    except (ValueError, TypeError) as e:
        return _error(400, str(e), request_id)

    entities_filter = data.get("entities")
    allow_list = data.get("allow_list")
    threshold = data.get("score_threshold")

    processor = _get_image_pipeline()
    cache_key = hashlib.sha256(image_bytes).hexdigest()
    with _state["image_lock"]:
        try:
            if anonymize:
                result = processor.process(
                    image=image, mosaic_style=style, fill_color=fill_color,
                    entities=entities_filter, allow_list=allow_list,
                    score_threshold=threshold, ocr_cache_key=cache_key)
                found = [e.to_dict() for e in result.pii_entities]
                buffer = io.BytesIO()
                result.processed_image.save(buffer, format="PNG")
                out_b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
            else:
                found = [e.to_dict() for e in processor.analyze_only(
                    image=image, entities=entities_filter, allow_list=allow_list,
                    score_threshold=threshold, ocr_cache_key=cache_key)]
                out_b64 = None
            ocr_text, ocr_confidence = _ocr_payload()
        except Exception as e:
            from cn_pii_anonymization.utils.exceptions import (
                OCRError,
                PIIRecognitionError,
            )
            if isinstance(e, OCRError):
                return _error(503, f"OCR 引擎不可用: {e}", request_id)
            if isinstance(e, PIIRecognitionError):
                return _error(500, f"PII 识别失败: {e}", request_id)
            raise

    payload = {
        "pii_entities": found,
        "by_label": _entity_counts(found, "entity_type"),
        "entity_count": len(found),
        "ocr_text": ocr_text,
        "ocr_confidence": ocr_confidence,
        "image_size": {"width": image.width, "height": image.height},
    }
    if anonymize:
        payload = {"image_base64": out_b64, "mime_type": "image/png", **payload}
    else:
        payload["has_pii"] = bool(found)
    return _json_response(200, {"success": True, "data": payload, "error": None,
                                "request_id": request_id})


def run(config, modules):
    """服务主入口

    Args:
        config: 来自 config.toml 的配置字典
        modules: 模块命名空间字典
    """
    _state["config"] = config
    _state["started_at"] = time.time()

    log_mod = modules.get("log-enhancer")

    def _log(msg, level="INFO"):
        if log_mod:
            log_mod.log(msg, level=level)
        else:
            getattr(logger, level.lower(), logger.info)(msg)

    _apply_env_overrides(config)
    _log(f"源模块日志级别: {_configure_source_logging(config)}")
    _check_environment(_log)

    def handle_health():
        """GET /health — 只读状态快照，绝不触发引擎初始化"""
        engine = _state["aiguard"]
        text_enabled = _cfg("aiguard", "enabled", True)
        image_enabled = _cfg("image", "enabled", True)
        loaded = bool(engine and engine.is_loaded())
        text_ok = (not text_enabled) or loaded
        image_ok = (not image_enabled) or _state["image_ready"]
        return {
            "status": "ok" if (text_ok and image_ok) else "degraded",
            "uptime_seconds": int(time.time() - _state["started_at"]),
            "text": {
                "enabled": text_enabled,
                "loaded": loaded,
                "device": (engine._device if loaded else _state["env"].get("aiguard_device")),
                "init_error": engine.get_init_error() if engine else _state["aiguard_error"],
                "cache": engine.get_cache_stats() if engine else None,
                "cache_size": int(_cfg("aiguard", "cache_size", 64)),
            },
            "image": {
                "enabled": image_enabled,
                "ready": _state["image_ready"],
                "init_error": _state["image_error"],
                "ocr_cache_size": int(_cfg("image", "ocr_cache_size", 64)),
                "mosaic_styles": list(MOSAIC_STYLES),
            },
            "environment": _state.get("env", {}),
        }

    def handle_text_anonymize(request_info):
        """POST /text/anonymize — AIguard 文本脱敏"""
        request_id = _new_request_id()
        _log(f"[REQUEST_START] ID={request_id} path=/text/anonymize")
        try:
            resp = _handle_text(request_info, request_id, anonymize=True)
            _log(f"[TEXT_DONE] ID={request_id} status={resp['status_code']}")
            return resp
        except Exception as e:
            _log(f"[UNEXPECTED_ERROR] ID={request_id} {type(e).__name__}: {e}", "ERROR")
            return _error(500, f"服务内部错误: {e}", request_id)
        finally:
            _log(f"[REQUEST_END] ID={request_id}")

    def handle_text_analyze(request_info):
        """POST /text/analyze — AIguard 文本仅识别"""
        request_id = _new_request_id()
        _log(f"[REQUEST_START] ID={request_id} path=/text/analyze")
        try:
            return _handle_text(request_info, request_id, anonymize=False)
        except Exception as e:
            _log(f"[UNEXPECTED_ERROR] ID={request_id} {type(e).__name__}: {e}", "ERROR")
            return _error(500, f"服务内部错误: {e}", request_id)
        finally:
            _log(f"[REQUEST_END] ID={request_id}")

    def handle_image_anonymize(request_info):
        """POST /image/anonymize — 图像脱敏（base64 进出）"""
        request_id = _new_request_id()
        _log(f"[REQUEST_START] ID={request_id} path=/image/anonymize")
        try:
            return _handle_image(request_info, request_id, anonymize=True)
        except Exception as e:
            _log(f"[UNEXPECTED_ERROR] ID={request_id} {type(e).__name__}: {e}", "ERROR")
            return _error(500, f"服务内部错误: {e}", request_id)
        finally:
            _log(f"[REQUEST_END] ID={request_id}")

    def handle_image_analyze(request_info):
        """POST /image/analyze — 图像仅识别"""
        request_id = _new_request_id()
        _log(f"[REQUEST_START] ID={request_id} path=/image/analyze")
        try:
            return _handle_image(request_info, request_id, anonymize=False)
        except Exception as e:
            _log(f"[UNEXPECTED_ERROR] ID={request_id} {type(e).__name__}: {e}", "ERROR")
            return _error(500, f"服务内部错误: {e}", request_id)
        finally:
            _log(f"[REQUEST_END] ID={request_id}")

    web_mod = modules.get("web-service")
    if web_mod:
        web_mod.register_page("/", _TEST_PAGE_HTML)
        web_mod.register_api("/health", handle_health)
        web_mod.register_handler("/text/anonymize", "POST", handle_text_anonymize)
        web_mod.register_handler("/text/analyze", "POST", handle_text_analyze)
        web_mod.register_handler("/image/anonymize", "POST", handle_image_anonymize)
        web_mod.register_handler("/image/analyze", "POST", handle_image_analyze)

        port_info = web_mod.get_port()
        if port_info.get("success"):
            _log(f"Privacy Filter 服务路由已注册: {port_info['data']['base_url']}")
    else:
        _log("web-service 模块未启用，HTTP 接口不可用", "ERROR")
        return

    _preload(_log)
    _log("Privacy Filter 服务就绪（文本 AIguard + 图像 PaddleOCR）")

    while True:
        time.sleep(60)


def on_config_reload(new_config):
    """配置热重载：替换配置引用。

    设备、模型、source_path、缓存大小、分块长度与日志级别已固化进引擎实例与环境
    变量，需重启服务生效。
    """
    _state["config"] = new_config
    logger.info("配置已重新加载（设备/模型/source_path/缓存大小/分块长度/日志级别变更需重启服务）")


def on_shutdown():
    """服务关闭回调"""
    logger.info("Privacy Filter 脱敏服务正在关闭")


def _new_request_id():
    """生成毫秒级请求 ID。"""
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
