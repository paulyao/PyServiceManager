# LLM 模型别名映射

## 适用场景
- 对外暴露统一的模型名称（如 `gpt-4`、`deepseek-chat`），内部映射到实际供应商模型 ID
- 支持多供应商模型别名，便于切换底层模型而不影响调用方
- 提供模型列表查询接口，方便前端展示可用模型

## 核心设计

### 1. 模型映射配置（config.toml）
```toml
[llm.model_mapping]
# 别名 -> 实际模型ID
"gpt-4" = "qwen-max"
"gpt-3.5" = "deepseek-v3"
"deepseek-chat" = "deepseek-v3"
"deepseek-reasoner" = "deepseek-r1"
"default" = "deepseek-v3"       # 未匹配时的兜底模型
```

### 2. 映射解析逻辑
```python
class ModelMapper:
    def __init__(self, mapping_config, default_model):
        self._mapping = mapping_config  # {"gpt-4": "qwen-max", ...}
        self._default = default_model

    def resolve(self, alias):
        """将别名解析为实际模型 ID。

        Args:
            alias: 调用方传入的模型名称

        Returns:
            tuple: (actual_model_id, is_mapped: bool)
        """
        if not alias:
            return self._default, False
        actual = self._mapping.get(alias)
        if actual:
            return actual, True
        # 未匹配映射表，原样透传（允许直接使用供应商模型 ID）
        return alias, False

    def list_models(self):
        """返回所有可用模型（别名 + 实际 ID），供前端展示。"""
        models = []
        for alias, actual in self._mapping.items():
            if alias != "default":
                models.append({
                    "alias": alias,
                    "actual": actual,
                    "is_default": actual == self._default,
                })
        return models
```

### 3. 在请求处理中使用
```python
def handle_chat(request_info):
    data, err_resp = _parse_body(request_info)
    if err_resp:
        return err_resp

    # 解析模型别名
    requested_model = data.get("model")  # 调用方传入 "gpt-4"
    actual_model, is_mapped = model_mapper.resolve(requested_model)

    if is_mapped:
        log(f"模型映射: {requested_model} -> {actual_model}")

    result = call_llm(data["messages"], log, model=actual_model)
    return _json_response(200, {"success": True, "data": result})
```

### 4. 模型列表查询接口
```python
def handle_models():
    """GET /models — 返回可用模型列表。"""
    return {
        "models": model_mapper.list_models(),
        "default": model_mapper._default,
    }

# 注册路由
web_mod.register_api("/models", handle_models)
```

## 注意事项
- 映射表未匹配时**透传原始名称**而非报错，因为调用方可能直接使用供应商模型 ID
- `default` 键仅作为兜底，不出现在 `list_models()` 返回列表中
- 模型映射支持热重载（`on_config_reload`），修改 config.toml 后无需重启
- 日志记录映射关系（`gpt-4 -> qwen-max`），便于排查模型路由问题
- 配置变更后 `list_models()` 应立即反映最新映射，无需缓存
