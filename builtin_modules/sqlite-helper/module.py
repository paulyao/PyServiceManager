"""SQLite Helper Module - 通用 SQLite 读写模块

每次调用打开/关闭连接（SQLite 文件级锁，无需连接池）。
query 返回 dict 格式行，与 mysql-helper 保持一致的 API 模式。

Usage in service code:
    sqlite_mod = modules.get("sqlite-helper")

    # 建表
    sqlite_mod.execute(
        db_path="/path/to/db.sqlite",
        sql="CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT)",
    )

    # 插入（默认自动提交）
    sqlite_mod.execute(
        db_path="/path/to/db.sqlite",
        sql="INSERT OR REPLACE INTO users VALUES (?, ?)",
        params=(1, "alice"),
    )

    # 查询
    result = sqlite_mod.query(
        db_path="/path/to/db.sqlite",
        sql="SELECT * FROM users WHERE name = ?",
        params=("alice",),
    )
    rows = result["data"]["rows"]

    # 批量插入
    sqlite_mod.batch_insert(
        db_path="/path/to/db.sqlite",
        sql="INSERT OR REPLACE INTO users VALUES (?, ?)",
        rows=[(1, "alice"), (2, "bob")],
    )

注意：execute 的 commit 参数默认 True（DML 自动提交落盘）；
需要多条语句组成显式事务时传 commit=False 并自行控制提交。
连接统一开启 WAL 与 busy_timeout（不可用时自动降级）。
"""

import contextlib
import sqlite3


def _connect(db_path):
    """打开 SQLite 连接：Row 工厂 + WAL + busy_timeout（失败降级）。"""
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
    except sqlite3.Error:
        pass  # 只读文件系统等场景降级
    return conn

class Module:
    name = "sqlite-helper"
    version = "1.0.0"
    description = "Generic SQLite read/write module"

    def __init__(self):
        self._logger = None

    def execute(self, db_path, sql, params=None, commit=True):
        """执行单条 SQL（DDL/DML），返回影响行数。

        Args:
            db_path: SQLite 数据库文件路径。
            sql: SQL 语句，使用 ? 占位符。
            params: 参数元组，可选。
            commit: 是否提交事务，默认 True（DML 自动落盘；
                    显式多语句事务时可传 False 并自行控制）。

        Returns:
            dict: {"success": bool, "data": {"rowcount": int}, "error": str|None}
        """
        try:
            with contextlib.closing(_connect(db_path)) as conn:
                cursor = conn.execute(sql, params or ())
                rowcount = cursor.rowcount
                if commit:
                    conn.commit()
            return {
                "success": True,
                "data": {"rowcount": rowcount},
                "error": None,
            }
        except Exception as e:
            if self._logger:
                self._logger.error(f"[{self.name}] execute error: {e}")
            return {
                "success": False,
                "data": {"rowcount": 0},
                "error": str(e),
            }

    def query(self, db_path, sql, params=None):
        """执行查询，返回行列表（dict 格式）。

        Args:
            db_path: SQLite 数据库文件路径。
            sql: SELECT SQL 语句，使用 ? 占位符。
            params: 参数元组，可选。

        Returns:
            dict: {"success": bool, "data": {"rows": list[dict]}, "error": str|None}
        """
        try:
            with contextlib.closing(_connect(db_path)) as conn:
                cursor = conn.execute(sql, params or ())
                rows = [dict(row) for row in cursor.fetchall()]
            return {
                "success": True,
                "data": {"rows": rows},
                "error": None,
            }

        except Exception as e:
            if self._logger:
                self._logger.error(f"[{self.name}] query error: {e}")
            return {
                "success": False,
                "data": {"rows": []},
                "error": str(e),
            }

    def query_one(self, db_path, sql, params=None):
        """执行查询，返回单行（dict 格式）。

        Args:
            db_path: SQLite 数据库文件路径。
            sql: SELECT SQL 语句，使用 ? 占位符。
            params: 参数元组，可选。

        Returns:
            dict: {"success": bool, "data": {"row": dict|None}, "error": str|None}
        """
        try:
            with contextlib.closing(_connect(db_path)) as conn:
                cursor = conn.execute(sql, params or ())
                row = cursor.fetchone()
            return {
                "success": True,
                "data": {"row": dict(row) if row else None},
                "error": None,
            }
        except Exception as e:
            if self._logger:
                self._logger.error(f"[{self.name}] query_one error: {e}")
            return {
                "success": False,
                "data": {"row": None},
                "error": str(e),
            }

    def batch_insert(self, db_path, sql, rows):
        """批量插入（executemany）。

        Args:
            db_path: SQLite 数据库文件路径。
            sql: INSERT SQL 语句，使用 ? 占位符。
            rows: 参数元组列表，如 [(1, "alice"), (2, "bob")]。

            dict: {"success": bool, "data": {"rowcount": int}, "error": str|None}
        """
        try:
            with contextlib.closing(_connect(db_path)) as conn:
                cursor = conn.executemany(sql, rows)
                rowcount = cursor.rowcount
                conn.commit()
            return {
                "success": True,
                "data": {"rowcount": rowcount},
                "error": None,
            }
        except Exception as e:
            if self._logger:
                self._logger.error(f"[{self.name}] batch_insert error: {e}")
            return {
                "success": False,
                "data": {"rowcount": 0},
                "error": str(e),
            }
