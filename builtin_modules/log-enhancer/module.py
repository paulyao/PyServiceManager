"""Log Enhancer module - enhanced logging with log retention management."""

import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path


# 匹配以 "YYYY-MM-DD" 开头的日志行（runner 日志格式）
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

class Module:
    name = "log-enhancer"
    version = "1.1.0"
    description = "Enhanced logging module with log retention management"

    # Defaults
    DEFAULT_RETENTION_DAYS = 3
    DEFAULT_CHECK_INTERVAL = 3600  # seconds (1 hour)

    def __init__(self):
        self._timer = None
        self._stop_flag = threading.Event()
        self._retention_days = self.DEFAULT_RETENTION_DAYS
        self._check_interval = self.DEFAULT_CHECK_INTERVAL
        self._log_path = None
        self._logger = None

    def on_start(self, ctx):
        """Start the log retention cleanup thread."""
        # Read module config
        self._retention_days = ctx.module_config.get("retention", {}).get("days", self.DEFAULT_RETENTION_DAYS)
        self._check_interval = ctx.module_config.get("retention", {}).get("check_interval", self.DEFAULT_CHECK_INTERVAL)
        self._log_path = ctx.data_dir / "runner.log"
        self._logger = ctx.logger

        ctx.logger.info(
            f"Module {self.name} started - service: {ctx.service_name}, "
            f"retention: {self._retention_days} days"
        )

        # Start background cleanup thread
        self._stop_flag.clear()
        self._timer = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._timer.start()

    def on_stop(self, ctx):
        """Stop the cleanup thread."""
        self._stop_flag.set()
        if self._timer and self._timer.is_alive():
            self._timer.join(timeout=5)
        ctx.logger.info(f"Module {self.name} stopped")

    def on_config_reload(self, ctx):
        """Handle config reload."""
        self._retention_days = ctx.module_config.get("retention", {}).get("days", self.DEFAULT_RETENTION_DAYS)
        self._check_interval = ctx.module_config.get("retention", {}).get("check_interval", self.DEFAULT_CHECK_INTERVAL)
        ctx.logger.info(
            f"Module {self.name} config reloaded - retention: {self._retention_days} days"
        )

    def on_error(self, ctx, error):
        """Handle service error."""
        ctx.logger.error(f"Module {self.name} caught service error: {error}")

    def log(self, message, level="INFO"):
        """Log a message - accessible via modules["log-enhancer"].log()"""
        if self._logger is not None:
            # 走服务日志框架（与 runner 的日志格式/落盘一致）
            log_fn = getattr(self._logger, level.lower(), self._logger.info)
            log_fn(message)
        else:
            # on_start 未执行时的降级路径（直接运行/测试场景）
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"{timestamp} [{level}] {message}")

    def _cleanup_loop(self):
        """Background loop that periodically cleans up old log entries."""
        # Run cleanup once at startup
        self._run_cleanup()

        while not self._stop_flag.is_set():
            self._stop_flag.wait(timeout=self._check_interval)
            if self._stop_flag.is_set():
                break
            self._run_cleanup()

    def _run_cleanup(self):
        """Remove log entries older than retention_days.

        日志按时间顺序追加：旧行在文件头部，无法用 truncate 去头。
        采用原子替换：保留内容写入同目录临时文件后 os.replace——
        - 读取端（log streamer）要么看到完整旧文件、要么完整新文件，
          永不读到半截内容（原子替换语义）
        - 追加端（runner）持有旧 inode 继续写，其新行在下次清理周期/
          服务重启后归位；不会出现撕裂或交错写入
        无日期前缀的行视为保留（连续行/print 输出）。
        """
        if not self._log_path or not self._log_path.exists():
            return

        try:
            cutoff = datetime.now() - timedelta(days=self._retention_days)
            cutoff_str = cutoff.strftime("%Y-%m-%d")

            # 二进制 + 逐行 readline：字节偏移精确（文本模式 tell() 在
            # 迭代中禁用且偏移不透明）
            remove_end = 0
            removed_count = 0
            with open(self._log_path, "rb") as f:
                while True:
                    raw = f.readline()
                    if not raw:
                        break
                    line = raw.decode("utf-8", errors="replace")
                    if not _DATE_RE.match(line):
                        # 无日期前缀：保守保留，停止扫描
                        break
                    if line[:10] >= cutoff_str:
                        # 首个新行：停止扫描
                        break
                    remove_end = f.tell()
                    removed_count += 1

            if removed_count > 0 and remove_end > 0:
                # 保留 remove_end 之后的全部内容，原子替换原文件
                with open(self._log_path, "rb") as f:
                    f.seek(remove_end)
                    kept = f.read()
                tmp_path = self._log_path.with_name(
                    self._log_path.name + ".cleanup.tmp")
                with open(tmp_path, "wb") as out:
                    out.write(kept)
                os.replace(tmp_path, self._log_path)
                self._logger.info(
                    f"Log cleanup: removed {removed_count} entries older than "
                    f"{self._retention_days} days"
                )
        except Exception as e:
            self._logger.error(f"Log cleanup error: {e}")
