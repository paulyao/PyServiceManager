"""MySQL Helper module - generic MySQL read/write operations.

Connection parameters and SQL are passed by the caller at invocation time,
making this module reusable across different services and databases.

Usage in service code:
    result = modules["mysql-helper"].execute(
        host="127.0.0.1", port=3306, user="root", password="secret",
        database="mydb", sql="SELECT * FROM users WHERE id = %s",
        params=(1,)
    )

    result = modules["mysql-helper"].execute(
        host="127.0.0.1", port=3306, user="root", password="secret",
        database="mydb", sql="INSERT INTO users (name) VALUES (%s)",
        params=("alice",), commit=True
    )
"""

import pymysql
from pymysql.cursors import DictCursor


class Module:
    name = "mysql-helper"
    version = "1.0.0"
    description = "Generic MySQL read/write module with caller-provided connection and SQL"

    def on_start(self, ctx):
        ctx.logger.info(f"Module {self.name} started - service: {ctx.service_name}")

    def on_stop(self, ctx):
        ctx.logger.info(f"Module {self.name} stopped")

    def on_config_reload(self, ctx):
        ctx.logger.info(f"Module {self.name} config reloaded")

    def execute(self, *, host, port=3306, user, password, database,
                sql, params=None, commit=False, charset="utf8mb4",
                connect_timeout=10, read_timeout=30, write_timeout=30):
        """Execute a single SQL statement.

        Args:
            host: MySQL server host.
            port: MySQL server port, default 3306.
            user: Database user.
            password: Database password.
            database: Database name.
            sql: SQL statement with optional %s placeholders.
            params: Tuple of parameters for prepared statement.
            commit: Whether to commit (for INSERT/UPDATE/DELETE), default False.
            charset: Connection charset, default utf8mb4.
            connect_timeout: Connection timeout in seconds, default 10.
            read_timeout: Read timeout in seconds, default 30.
            write_timeout: Write timeout in seconds, default 30.

        Returns:
            dict: {
                "success": bool,
                "rows": list[dict] | None,   -- for SELECT queries
                "rowcount": int | None,       -- affected rows for DML
                "lastrowid": int | None,      -- last auto-increment id for INSERT
                "error": str | None           -- error message if failed
            }
        """
        connection = None
        try:
            connection = pymysql.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
                charset=charset,
                cursorclass=DictCursor,
                connect_timeout=connect_timeout,
                read_timeout=read_timeout,
                write_timeout=write_timeout,
            )
            with connection.cursor() as cursor:
                cursor.execute(sql, params)

                if sql.strip().upper().startswith("SELECT"):
                    rows = cursor.fetchall()
                    result = {
                        "success": True,
                        "rows": rows,
                        "rowcount": len(rows),
                        "lastrowid": None,
                        "error": None,
                    }
                else:
                    if commit:
                        connection.commit()
                    result = {
                        "success": True,
                        "rows": None,
                        "rowcount": cursor.rowcount,
                        "lastrowid": cursor.lastrowid or None,
                        "error": None,
                    }
            return result

        except Exception as e:
            if connection and commit:
                try:
                    connection.rollback()
                except Exception:
                    pass
            return {
                "success": False,
                "rows": None,
                "rowcount": None,
                "lastrowid": None,
                "error": str(e),
            }
        finally:
            if connection:
                try:
                    connection.close()
                except Exception:
                    pass

    def execute_many(self, *, host, port=3306, user, password, database,
                     sql, params_list=None, charset="utf8mb4",
                     connect_timeout=10, read_timeout=30, write_timeout=30):
        """Execute a SQL statement with multiple parameter sets (batch mode).

        Args:
            host: MySQL server host.
            port: MySQL server port, default 3306.
            user: Database user.
            password: Database password.
            database: Database name.
            sql: SQL statement with %s placeholders.
            params_list: List of parameter tuples for executemany.
            charset: Connection charset, default utf8mb4.
            connect_timeout: Connection timeout in seconds.
            read_timeout: Read timeout in seconds.
            write_timeout: Write timeout in seconds.

        Returns:
            dict: {
                "success": bool,
                "rowcount": int | None,
                "error": str | None
            }
        """
        connection = None
        try:
            connection = pymysql.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
                charset=charset,
                cursorclass=DictCursor,
                connect_timeout=connect_timeout,
                read_timeout=read_timeout,
                write_timeout=write_timeout,
            )
            with connection.cursor() as cursor:
                cursor.executemany(sql, params_list or [])
                connection.commit()
                return {
                    "success": True,
                    "rowcount": cursor.rowcount,
                    "error": None,
                }
        except Exception as e:
            if connection:
                try:
                    connection.rollback()
                except Exception:
                    pass
            return {
                "success": False,
                "rowcount": None,
                "error": str(e),
            }
        finally:
            if connection:
                try:
                    connection.close()
                except Exception:
                    pass

    def test_connection(self, *, host, port=3306, user, password,
                        charset="utf8mb4", connect_timeout=10):
        """Test MySQL connection without executing any SQL.

        Returns:
            dict: {"success": bool, "error": str | None}
        """
        try:
            connection = pymysql.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                charset=charset,
                connect_timeout=connect_timeout,
            )
            connection.close()
            return {"success": True, "error": None}
        except Exception as e:
            return {"success": False, "error": str(e)}
