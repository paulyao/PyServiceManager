"""Log Enhancer module - enhanced logging with log retention management."""

import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path


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
        print(f"[{level}] {message}")

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
        """Remove log entries older than retention_days."""
        if not self._log_path or not self._log_path.exists():
            return

        try:
            cutoff = datetime.now() - timedelta(days=self._retention_days)
            cutoff_str = cutoff.strftime("%Y-%m-%d")

            with open(self._log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            # Keep lines that are newer than cutoff or don't have a timestamp
            kept = []
            removed_count = 0
            for line in lines:
                # Log lines start with "YYYY-MM-DD HH:MM:SS,..."
                line_date = line[:10] if len(line) >= 10 else ""
                if line_date >= cutoff_str:
                    kept.append(line)
                elif line_date and line[4] == "-" and line[7] == "-":
                    # Valid date format and older than cutoff
                    removed_count += 1
                else:
                    # No valid date prefix (e.g. continuation line, print output) - keep
                    kept.append(line)

            if removed_count > 0:
                with open(self._log_path, "w", encoding="utf-8") as f:
                    f.writelines(kept)
                self._logger.info(
                    f"Log cleanup: removed {removed_count} entries older than {self._retention_days} days"
                )
        except Exception as e:
            self._logger.error(f"Log cleanup error: {e}")
