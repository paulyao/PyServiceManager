"""LLM-API-proxy 服务一键部署脚本

通过平台 API 完成：创建服务 → 写入配置 → 安装依赖 → 绑定模块 → 启动服务。
幂等：服务已存在时跳过创建，改为更新代码。

Usage: python deploy.py [--start]
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

API_BASE = "http://localhost:8900/api/v1"
SERVICE_NAME = "LLM-API-proxy"
SERVICE_DIR = Path(__file__).resolve().parent
REQUIRED_MODULES = ["log-enhancer", "web-service"]
REQUIREMENTS = ["openai>=1.0"]


def api(method, path, payload=None):
    """调用平台 API，返回 (status_code, body_dict|None)。"""
    url = API_BASE + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"detail": body}
    except urllib.error.URLError as e:
        print(f"[FATAL] 无法连接平台 API: {e}")
        sys.exit(1)


def main():
    code = (SERVICE_DIR / "main.py").read_text(encoding="utf-8")
    config = (SERVICE_DIR / "config.toml").read_text(encoding="utf-8")

    # 1. 创建服务（已存在则更新代码）
    status, body = api("POST", "/services", {
        "name": SERVICE_NAME,
        "display_name": "LLM API 代理",
        "description": "DashScope LLM 代理与 JAR license 查询服务（移植自 DeepSeek Flask 项目）",
        "code": code,
    })
    if status == 201:
        print(f"[OK] 服务 {SERVICE_NAME} 创建成功")
    elif status == 400 and "exists" in str(body.get("detail", "")):
        print(f"[SKIP] 服务已存在，更新代码")
        status, body = api("PUT", f"/services/{SERVICE_NAME}/code", {"code": code})
        if status != 200:
            print(f"[FAIL] 更新代码失败: {body}")
            sys.exit(1)
    else:
        print(f"[FAIL] 创建服务失败 ({status}): {body}")
        sys.exit(1)

    # 声明依赖（创建接口不保存 requirements，需单独 PUT）
    status, body = api("PUT", f"/services/{SERVICE_NAME}", {"requirements": REQUIREMENTS})
    if status == 200:
        print(f"[OK] 依赖声明成功: {REQUIREMENTS}")
    else:
        print(f"[FAIL] 依赖声明失败 ({status}): {body}")
        sys.exit(1)

    # 2. 写入配置（创建服务时平台生成默认模板，需覆盖）
    status, body = api("PUT", f"/services/{SERVICE_NAME}/config", {"config": config})
    if status == 200:
        print("[OK] 配置写入成功")
    else:
        print(f"[FAIL] 配置写入失败 ({status}): {body}")
        sys.exit(1)

    # 3. 安装依赖
    status, body = api("POST", f"/services/{SERVICE_NAME}/deps/install")
    if status == 200:
        print(f"[OK] 依赖安装完成: {body}")
    else:
        print(f"[WARN] 依赖安装异常 ({status}): {body}")

    # 4. 绑定模块
    status, modules = api("GET", "/modules")
    if status != 200:
        print(f"[FAIL] 获取模块列表失败 ({status})")
        sys.exit(1)
    module_ids = {m["name"]: m["id"] for m in modules}
    bindings = []
    for order, name in enumerate(REQUIRED_MODULES):
        if name not in module_ids:
            print(f"[FAIL] 平台缺少模块: {name}")
            sys.exit(1)
        bindings.append({"module_id": module_ids[name], "enabled": True, "load_order": order})
    status, body = api("PUT", f"/modules/service/{SERVICE_NAME}", {"modules": bindings})
    if status == 200:
        print(f"[OK] 模块绑定成功: {REQUIRED_MODULES}")
    else:
        print(f"[FAIL] 模块绑定失败 ({status}): {body}")
        sys.exit(1)

    # 5. 启动服务（可选，需先配置 api_key）
    if "--start" in sys.argv:
        status, body = api("POST", f"/services/{SERVICE_NAME}/start")
        if status == 200:
            print(f"[OK] 服务已启动: status={body.get('status')}")
        else:
            print(f"[FAIL] 服务启动失败 ({status}): {body}")
            sys.exit(1)
    else:
        print("[INFO] 请先在平台 Web 界面填写 [llm].api_key，再启动服务")


if __name__ == "__main__":
    main()
