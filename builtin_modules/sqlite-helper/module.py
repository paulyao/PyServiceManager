"""SQLite Helper Module - 通用 SQLite 读写模块

每次调用打开/关闭连接（SQLite 文件级锁，无需连接池）。
query 返回 dict 格式行，与 mysql-helper 保持一致的 API 模式。

Usage in service code:
    sqlite_mod = modules.get("sqlite-helper")

    # 建表
    sqlite_mod.execute(
        db_path="/path/to/db.sqlite",
        sql="CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT)",
        commit=True,
    )

    # 插入
    sqlite_mod.execute(
        db_path="/path/to/db.sqlite",
        sql="INSERT OR REPLACE INTO users VALUES (?, ?)",
        params=(1, "alice"),
        commit=True,
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
"""

import sqlite3


class Module:
    name = "sqlite-helper"
    version = "1.0.0"
    description = "Generic SQLite read/write module"

    def __init__(self):
        self._logger = None

    def on_start(self, ctx):
        self._logger = ctx.logger
        ctx.logger.info(f"Module {self.name} started - service: {ctx.service_name}")

    def on_stop(self, ctx):
        ctx.logger.info(f"Module {self.name} stopped")

    def on_config_reload(self, ctx):
        self._logger = ctx.logger
        ctx.logger.info(f"Module {self.name} config reloaded")

    def execute(self, db_path, sql, params=None, commit=False):
        """执行单条 SQL（DDL/DML），返回影响行数。

        Args:
            db_path: SQLite 数据库文件路径。
            sql: SQL 语句，使用 ? 占位符。
            params: 参数元组，可选。
            commit: 是否提交事务，默认 False。

        Returns:
            dict: {"success": bool, "data": {"rowcount": int}, "error": str|None}
        """
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.execute(sql, params or ())
            rowcount = cursor.rowcount
            if commit:
                conn.commit()
            conn.close()
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
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(sql, params or ())
            rows = [dict(row) for row in cursor.fetchall()]
            conn.close()
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
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(sql, params or ())
            row = cursor.fetchone()
            conn.close()
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

        Returns:
            dict: {"success": bool, "data": {"rowcount": int}, "error": str|None}
        """
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.executemany(sql, rows)
            rowcount = cursor.rowcount
            conn.commit()
            conn.close()
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
