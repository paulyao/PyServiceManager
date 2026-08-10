# API 响应本地数据交叉增强

## 适用场景
- 从第三方 API 拉取数据后，需要与本地数据库中的字段（如自定义名称、备注）进行关联
- 在不改变原始 API 数据结构的前提下，为每条记录补充本地信息

## 核心步骤

### 1. 构建本地查找表（字典映射）
```python
def _build_local_names(table_name):
    """从本地 SQLite 构建 email -> name 映射。"""
    try:
        result = sqlite_mod.query(db_path=_DB_FILE,
            sql=f"SELECT email, name FROM {table_name}")
        return {
            r.get("email", ""): r.get("name", "")
            for r in result.get("data", {}).get("rows", [])
            if r.get("name")   # 过滤空值
        }
    except Exception:
        return {}
```

### 2. 在 API 数据上 merge 本地字段
```python
# 拉取 API 数据
api_members = fetch_from_api(...)

# 构建本地映射
local_names = _build_local_names("qoder_accounts")

# 为每条记录补充 localName（不覆盖原始 name）
enriched = [
    dict(m, localName=local_names.get(m.get("email", ""), ""))
    for m in api_members
]
```

### 3. 前端展示逻辑（优先显示差异）
```javascript
// 当 API 名称与本地名称不一致时，同时显示两者
const displayName = (m.localName && m.localName !== m.name)
    ? `${m.name}(${m.localName})`   // API名(本地名)
    : (m.name || '-');
```

## 注意事项
- 使用 `dict(m, localName=...)` 浅拷贝，不修改原始数据对象
- 本地映射构建失败时返回空字典，保证接口仍能正常返回（降级而非报错）
- 字段命名应区分来源（如 `name` 来自 API，`localName` 来自本地 DB），避免混淆
- 若本地数据可能为空字符串，在映射构建时提前过滤（`if r.get("name")`）
