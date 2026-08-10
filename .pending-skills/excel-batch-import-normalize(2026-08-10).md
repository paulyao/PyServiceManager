# Excel 批量导入与邮箱标准化清洗

## 适用场景
- 从 Excel（.xlsx/.xls）批量导入用户/账号数据到 SQLite 数据库
- 导入数据中邮箱格式不规范（含空格、大小写混杂），需标准化清洗
- 需幂等导入：相同邮箱的记录更新而非重复插入

## 核心设计

### 1. Excel 读取（openpyxl）
```python
import openpyxl

def read_excel_accounts(file_path):
    """读取 Excel 文件，返回记录列表。

    支持的列：邮箱(必须)、姓名、部门、角色 等
    """
    wb = openpyxl.load_workbook(file_path, read_only=True)
    ws = wb.active

    # 首行为表头，构建列名→索引映射
    headers = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
    col_map = {h.strip().lower(): i for i, h in enumerate(headers) if h}

    records = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        email = row[col_map.get("邮箱", col_map.get("email", 0))]
        if not email:
            continue  # 跳过无邮箱的行
        records.append({
            "email": _normalize_email(str(email)),
            "name": str(row[col_map.get("姓名", col_map.get("name", 1))] or "").strip(),
            "department": str(row[col_map.get("部门", col_map.get("department", 2))] or "").strip(),
            "role": str(row[col_map.get("角色", col_map.get("role", 3))] or "").strip(),
        })
    wb.close()
    return records
```

### 2. 邮箱标准化清洗
```python
import re

def _normalize_email(email):
    """邮箱标准化：去空格、转小写、去除特殊字符。

    Examples:
        " Alice@Example.COM " → "alice@example.com"
        "bob.smith＠company.com" → "bob.smith@company.com"  # 全角@替换
    """
    # 1. 去除首尾空格
    email = email.strip()
    # 2. 全角字符替换
    email = email.replace("＠", "@").replace("．", ".")
    # 3. 转小写
    email = email.lower()
    # 4. 去除中间空格（部分用户粘贴时带入）
    email = re.sub(r'\s+', '', email)
    # 5. 基本格式校验
    if not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
        raise ValueError(f"无效邮箱格式: {email}")
    return email
```

### 3. 幂等导入（UPSERT 语义）
```python
def import_accounts(sqlite_mod, db_path, records):
    """批量导入账号，邮箱去重（存在则更新，不存在则插入）。

    Returns:
        dict: {"inserted": int, "updated": int, "skipped": int, "errors": list}
    """
    stats = {"inserted": 0, "updated": 0, "skipped": 0, "errors": []}

    for rec in records:
        try:
            email = rec["email"]

            # 查询是否已存在
            existing = sqlite_mod.query(
                db_path=db_path,
                sql="SELECT id FROM accounts WHERE email = ?",
                params=[email],
            )
            rows = existing.get("data", {}).get("rows", [])

            if rows:
                # 存在 → 更新
                sqlite_mod.execute(
                    db_path=db_path,
                    sql="UPDATE accounts SET name=?, department=?, role=? WHERE email=?",
                    params=[rec["name"], rec["department"], rec["role"], email],
                )
                stats["updated"] += 1
            else:
                # 不存在 → 插入
                sqlite_mod.execute(
                    db_path=db_path,
                    sql="INSERT INTO accounts (email, name, department, role) VALUES (?, ?, ?, ?)",
                    params=[email, rec["name"], rec["department"], rec["role"]],
                )
                stats["inserted"] += 1

        except Exception as e:
            stats["skipped"] += 1
            stats["errors"].append({"email": rec.get("email", "?"), "error": str(e)})

    return stats
```

### 4. 导入结果摘要
```python
def handle_import(request_info):
    """POST /import — Excel 批量导入接口。"""
    # ... 文件上传解析 ...
    records = read_excel_accounts(uploaded_file_path)
    stats = import_accounts(sqlite_mod, _DB_FILE, records)

    summary = (
        f"导入完成: 新增 {stats['inserted']} 条, "
        f"更新 {stats['updated']} 条, "
        f"跳过 {stats['skipped']} 条"
    )
    if stats["errors"]:
        summary += f"\n失败记录: {json.dumps(stats['errors'], ensure_ascii=False)}"

    return _json_response(200, {"success": True, "summary": summary, "stats": stats})
```

## 注意事项
- Excel 列名匹配应**大小写不敏感**且支持中英文（`邮箱`/`email`），提高导入模板兼容性
- 邮箱清洗顺序很重要：先去空格再转小写再校验格式，颠倒顺序可能导致校验通过但存储不规范
- 全角字符（`＠`、`．`）是常见陷阱，用户从 IM 工具复制邮箱时常带入
- 单条导入失败不应阻断整个批量操作，记录错误后继续处理下一条
- 大量数据时考虑分批提交（每 100 条一次 `commit`），避免单条失败回滚全部
- 返回的 `stats` 应包含完整统计，便于前端展示导入结果摘要
