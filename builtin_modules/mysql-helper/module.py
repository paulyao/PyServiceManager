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
    """

    def __init__(self, max_idle=5, idle_timeout=300):
        self._pools = defaultdict(list)  # key -> [(conn, last_used_time), ...]
        self._lock = threading.Lock()
        self._max_idle = max_idle
        self._idle_timeout = idle_timeout

    def _make_key(self, host, port, user, password, database):
        return (host, port, user, password, database or "")

    def get(self, host, port, user, password, database, charset, connect_timeout,
            read_timeout=30, write_timeout=30):
        """Retrieve a connection from the pool, or create a new one."""
        key = self._make_key(host, port, user, password, database)
        with self._lock:
            pool = self._pools[key]
            while pool:
                conn, last_used = pool.pop()
                # Check idle timeout
                if time.time() - last_used > self._idle_timeout:
                    try:
                        conn.close()
                    except Exception:
                        pass
                    continue
                # Ping to verify the connection is still alive
                try:
                    conn.ping(reconnect=False)
                    return conn
                except Exception:
                    try:
                        conn.close()
                    except Exception:
                        pass
        # No usable connection in pool — create a new one
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
        )

    def put(self, conn, host, port, user, password, database):
        """Return a connection to the pool for reuse."""
        key = self._make_key(host, port, user, password, database)
        with self._lock:
            pool = self._pools[key]
            if len(pool) < self._max_idle:
                pool.append((conn, time.time()))
            else:
                try:
                    conn.close()
                except Exception:
                    pass

    def discard(self, conn):
        """Discard a broken connection (close without returning to pool)."""
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

    def update_config(self, max_idle=None, idle_timeout=None):
        """Update pool configuration dynamically."""
        with self._lock:
            if max_idle is not None:
                self._max_idle = max_idle
            if idle_timeout is not None:
                self._idle_timeout = idle_timeout

    def stats(self):
        """Return pool statistics: idle connection count per key."""
        with self._lock:
            return {
                repr(key): len(pool) for key, pool in self._pools.items() if pool
            }


class Module:
    name = "mysql-helper"
    version = "2.0.0"
    description = "Generic MySQL read/write module with connection pooling"

    def __init__(self):
        self._pool = None

    def on_start(self, ctx):
        max_idle = ctx.config.get("pool", {}).get("max_idle_connections", 5)
        idle_timeout = ctx.config.get("pool", {}).get("idle_timeout", 300)
        self._pool = ConnectionPool(max_idle=max_idle, idle_timeout=idle_timeout)
        ctx.logger.info(
            f"Module {self.name} started - service: {ctx.service_name}, "
            f"pool: max_idle={max_idle}, idle_timeout={idle_timeout}s"
        )

    def on_stop(self, ctx):
        if self._pool:
            self._pool.close_all()
            self._pool = None
        ctx.logger.info(f"Module {self.name} stopped - all connections closed")

    def on_config_reload(self, ctx):
        if self._pool:
            max_idle = ctx.config.get("pool", {}).get("max_idle_connections")
            idle_timeout = ctx.config.get("pool", {}).get("idle_timeout")
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
            # Return connection to pool on success
            self._pool.put(connection, host, port, user, password, database)
            return result

        except Exception as e:
            if connection and commit:
                try:
                    connection.rollback()
                except Exception:
                    pass
            # Discard broken connection (do not return to pool)
            if connection:
                self._pool.discard(connection)
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
            with connection.cursor() as cursor:
                cursor.executemany(sql, params_list or [])
                connection.commit()
            # Return connection to pool on success
            self._pool.put(connection, host, port, user, password, database)
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
                # Discard broken connection
                self._pool.discard(connection)
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
