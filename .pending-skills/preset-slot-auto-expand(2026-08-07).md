# 预置槽位与满载自动扩容

## 适用场景
- 需要预分配一批编号槽位（如邮箱 ai01-ai99）供后续分配使用
- 资源池容量有限，当所有槽位占满时需要自动扩容

## 核心步骤

### 1. 初始化预置槽位（缩容安全）
```python
def _init_preset_accounts():
    # 按当前需求量生成槽位（如 range(1, 88) 而非 range(1, 100)）
    rows = [(f"ai{i:02d}@wsgjp.com", "", "", None) for i in range(1, 88)]
    # INSERT OR IGNORE 保证幂等
    batch_insert(sql="INSERT OR IGNORE INTO accounts VALUES (?,?,?,?)", rows=rows)
    # 删除超出的空槽位：仅删除 name 为空的行，保留已分配记录
    for i in range(88, 100):
        execute(sql="DELETE FROM accounts WHERE email = ? AND (name = '' OR name IS NULL)",
                params=(f"ai{i:02d}@wsgjp.com",))
```

### 2. 自动生成新编号（满载扩容）
```python
import re

def _auto_generate_email(table, org):
    """查找表中邮箱数字最大值，+1 生成新邮箱。"""
    result = query(sql=f"SELECT email FROM {table}")
    max_num = 0
    for r in result.get("data", {}).get("rows", []):
        m = re.search(r"(\d+)", r.get("email", ""))
        if m:
            max_num = max(max_num, int(m.group(1)))
    prefix = "ai_cn" if org == "qoder_cn" else "ai"
    return f"{prefix}{max_num + 1:02d}@wsgjp.com"
```

### 3. 新增时优先复用空闲槽位
```python
def handle_add(email, name, department, role):
    # 先检查邮箱是否已存在
    existing = query_one(sql="SELECT email FROM table WHERE email = ?", params=(email,))
    if existing: update...

    # 检查是否有空闲槽位（name 为空）
    empty_slot = query_one(sql="SELECT email FROM table WHERE (name = '' OR name IS NULL) LIMIT 1")
    if empty_slot:
        return {"success": False, "error": "仍有空闲槽位，请使用空闲槽位的邮箱"}

    # 无空闲槽位：自动生成 max+1 邮箱
    new_email = _auto_generate_email(table, org)
    insert(sql="INSERT INTO table VALUES (?,?,?,?,?)", params=(new_email, name, ...))
    return {"success": True, "email": new_email}
```

## 注意事项
- 缩容时仅删除空槽位（`name = '' OR name IS NULL`），绝不删除已分配记录
- 使用 `INSERT OR IGNORE` 确保幂等，重启不重复插入
- 自动生成邮箱前返回给前端，让前端提示用户实际分配的邮箱
- 扩容逻辑同样适用于「自动分配」接口（如钉钉回调），无需人工干预
