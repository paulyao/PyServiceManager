"""MySQL Helper module - generic MySQL read/write operations.

Connection parameters and SQL are passed by the caller at invocation time,
making this module reusable across different services and databases.

Uses a connection pool to reuse connections and avoid the overhead of
creating a new connection on every call.

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

import threading
import time
from collections import defaultdict

import pymysql
from pymysql.cursors import DictCursor


class ConnectionPool:
    """Simple thread-safe connection pool keyed by connection parameters.

    Each unique (host, port, user, password, database) combination maintains
    an independent list of idle connections. Connections are validated before
    reuse and automatically closed after being idle for too long.

    Ping/reconnect network round-trips happen OUTSIDE the lock so one slow
    validation never blocks other borrowers. A hard cap (_max_total) limits
    total in-flight connections per key; exceeding it raises instead of queueing.
    """

    def __init__(self, max_idle=5, idle_timeout=300, max_total=20):
        self._pools = defaultdict(list)  # key -> [(conn, last_used_time), ...]
        self._active = defaultdict(int)  # key -> borrowed (in-flight) count
        self._lock = threading.Lock()
        self._max_idle = max_idle
        self._idle_timeout = idle_timeout
        self._max_total = max_total

    def _make_key(self, host, port, user, password, database):
        return (host, port, user, password, database or "")

    def get(self, host, port, user, password, database, charset, connect_timeout,
            read_timeout=30, write_timeout=30):
        """Retrieve a pooled (validated) connection or create a new one."""
        key = self._make_key(host, port, user, password, database)
        while True:
            conn = None
            with self._lock:
                pool = self._pools[key]
                while pool:
                    cand, last_used = pool.pop()
                    if time.time() - last_used > self._idle_timeout:
                        try:
                            cand.close()
                        except Exception:
                            pass
                        continue
                    conn = cand
                    break
                if conn is None:
                    if self._active[key] >= self._max_total:
                        raise RuntimeError(
                            f"MySQL connection limit reached ({self._max_total}) "
                            f"for {user}@{host}:{port}")
                    self._active[key] += 1
            if conn is None:
                # Create new connection (network I/O outside the lock)
                return pymysql.connect(
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
                    autocommit=True,  # 默认自动提交，避免隐式长事务
                )
            # Validate pooled connection (network I/O outside the lock)
            try:
                conn.ping(reconnect=True)
                conn.autocommit(True)
                return conn
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                # Release the active slot, then retry from the pool
                with self._lock:
                    self._active[key] -= 1
                continue

    def put(self, conn, host, port, user, password, database):
        """Return a connection to the pool for reuse."""
        key = self._make_key(host, port, user, password, database)
        with self._lock:
            self._active[key] -= 1
            pool = self._pools[key]
            if len(pool) < self._max_idle:
                # 确保归还的连接是干净的（autocommit=True，无未提交事务）
                try:
                    conn.autocommit(True)
                except Exception:
                    try:
                        conn.close()
                    except Exception:
                        pass
                    return
                pool.append((conn, time.time()))
            else:
                try:
                    conn.close()
                except Exception:
                    pass

    def discard(self, conn, host=None, port=None, user=None, password=None, database=None):
        """Discard a broken connection (close without returning to pool)."""
        if host is not None:
            key = self._make_key(host, port, user, password, database)
            with self._lock:
                self._active[key] -= 1
        try:
            conn.close()
        except Exception:
            pass

    def close_all(self):
        """Close every idle connection in all pools and clear the pools."""
        with self._lock:
            for pool in self._pools.values():
                for conn, _ in pool:
                    try:
                        conn.close()
                    except Exception:
                        pass
            self._pools.clear()
            self._active.clear()

    def update_config(self, max_idle=None, idle_timeout=None):
        """Update pool configuration dynamically."""
        with self._lock:
            if max_idle is not None:
                self._max_idle = max_idle
            if idle_timeout is not None:
                self._idle_timeout = idle_timeout

    def stats(self):
        """Return pool statistics: idle connection count per key (sanitized)."""
        with self._lock:
            out = {}
            for key, pool in self._pools.items():
                if not pool:
                    continue
                host, port, user, password, database = key
                label = f"{user}@{host}:{port}/{database or '-'}"
                out[label] = {
                    "idle": len(pool),
                    "active": self._active.get((host, port, user, password, database), 0),
                }
            return out


class Module:
    name = "mysql-helper"
    version = "2.1.0"
    description = "Generic MySQL read/write module with connection pooling"

    def __init__(self):
        self._pool = None

    def on_start(self, ctx):
        pool_cfg = ctx.module_config.get("pool", {})
        max_idle = pool_cfg.get("max_idle_connections", 5)
        idle_timeout = pool_cfg.get("idle_timeout", 300)
        max_total = pool_cfg.get("max_total_connections", 20)
        self._pool = ConnectionPool(
            max_idle=max_idle, idle_timeout=idle_timeout, max_total=max_total)
        ctx.logger.info(
            f"Module {self.name} started - service: {ctx.service_name}, "
            f"pool: max_idle={max_idle}, idle_timeout={idle_timeout}s, "
            f"max_total={max_total}"
        )

    def on_stop(self, ctx):
        if self._pool:
            self._pool.close_all()
            self._pool = None
        ctx.logger.info(f"Module {self.name} stopped - all connections closed")

    def on_config_reload(self, ctx):
        if self._pool:
            max_idle = ctx.module_config.get("pool", {}).get("max_idle_connections")
            idle_timeout = ctx.module_config.get("pool", {}).get("idle_timeout")
            self._pool.update_config(max_idle=max_idle, idle_timeout=idle_timeout)
            ctx.logger.info(
                f"Module {self.name} config reloaded - "
                f"pool: max_idle={max_idle}, idle_timeout={idle_timeout}s"
            )

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
            connection = self._pool.get(
                host, port, user, password, database, charset,
                connect_timeout, read_timeout, write_timeout,
            )
            with connection.cursor() as cursor:
                if commit:
                    # 显式事务：关闭 autocommit，执行，提交。
                    # 不在 finally 中恢复 autocommit：MySQL 开启 autocommit
                    # 会隐式提交当前事务——失败路径先 rollback（except 块），
                    # 成功路径由 put() 归还时统一重置。
                    connection.autocommit(False)
                    cursor.execute(sql, params)
                    connection.commit()
                else:
                    # autocommit=True 下直接执行（SELECT 不会持有事务）
                    cursor.execute(sql, params)

                # 结果集检测：有 description 才 fetch（覆盖 SELECT/WITH/SHOW/EXPLAIN）
                if cursor.description is not None:
                    rows = cursor.fetchall()
                    result = {
                        "success": True,
                        "rows": rows,
                        "rowcount": len(rows),
                        "lastrowid": None,
                        "error": None,
                    }
                else:
                    result = {
                        "success": True,
                        "rows": None,
                        "rowcount": cursor.rowcount,
                        "lastrowid": cursor.lastrowid or None,
                        "error": None,
                    }
            # Return connection to pool on success
            self._pool.put(connection, host, port, user, password, database)
            return result

        except Exception as e:
            if connection:
                try:
                    connection.rollback()
                except Exception:
                    pass
                # Discard broken connection (do not return to pool)
                self._pool.discard(
                    connection, host, port, user, password, database)
            return {
                "success": False,
                "rows": None,
                "rowcount": None,
                "lastrowid": None,
                "error": str(e),
            }

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
            connection = self._pool.get(
                host, port, user, password, database, charset,
                connect_timeout, read_timeout, write_timeout,
            )
            # 批量操作用显式事务；不在 finally 恢复 autocommit（会隐式提交
            # 失败事务的部分写入），归还路径 put() 统一重置
            connection.autocommit(False)
            with connection.cursor() as cursor:
                cursor.executemany(sql, params_list or [])
                rowcount = cursor.rowcount
                connection.commit()
            # Return connection to pool on success
            self._pool.put(connection, host, port, user, password, database)
            return {
                "success": True,
                "rowcount": rowcount,
                "error": None,
            }
        except Exception as e:
            if connection:
                try:
                    connection.rollback()
                except Exception:
                    pass
                # Discard broken connection
                self._pool.discard(
                    connection, host, port, user, password, database)
            return {
                "success": False,
                "rowcount": None,
                "error": str(e),
            }

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

    def close_all(self):
        """Close all connections in all pools."""
        if self._pool:
            self._pool.close_all()

    def pool_stats(self):
        """Return connection pool status information."""
        if self._pool:
            return self._pool.stats()
        return {}
