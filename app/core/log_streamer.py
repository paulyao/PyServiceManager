"""Log streaming via WebSocket and history retrieval."""
import asyncio
import logging
import time
from pathlib import Path
from typing import Set

from fastapi import WebSocket, WebSocketDisconnect

from app.config import SERVICES_DIR

logger = logging.getLogger(__name__)


class LogStreamer:
    """Reads service log files and streams them via WebSocket."""

    # Maximum concurrent WebSocket connections per service
    MAX_CONNECTIONS: int = 10
    # Maximum lifetime of a single WebSocket connection (seconds)
    MAX_CONNECTION_DURATION: float = 3600.0
    # Per-message send timeout (seconds)
    SEND_TIMEOUT: float = 10.0
    # Idle poll interval when no new log lines are available (seconds)
    POLL_INTERVAL: float = 0.2

    def __init__(self):
        self._active_connections: dict[str, Set[WebSocket]] = {}
        self._shutdown = False  # 关闭标志

    def _log_path(self, service_name: str) -> Path:
        return SERVICES_DIR / service_name / "runner.log"

    async def get_history(self, service_name: str, lines: int = 200) -> list[str]:
        """Get the last N lines from a service log file."""
        log_path = self._log_path(service_name)
        if not log_path.exists():
            return []

        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
                return [line.rstrip("\n") for line in all_lines[-lines:]]
        except Exception as exc:
            logger.error("Failed to read log history for %s: %s", service_name, exc)
            return []

    async def _safe_send(self, websocket: WebSocket, text: str) -> None:
        """Send text with a timeout to prevent stuck writes."""
        await asyncio.wait_for(websocket.send_text(text), timeout=self.SEND_TIMEOUT)

    async def stream_to_websocket(self, service_name: str, websocket: WebSocket):
        """Stream log updates to a WebSocket client."""
        await websocket.accept()

        # Enforce per-service maximum connection count
        existing = self._active_connections.get(service_name)
        if existing is not None and len(existing) >= self.MAX_CONNECTIONS:
            logger.warning(
                "Rejecting WebSocket for %s: max connections (%d) reached",
                service_name,
                self.MAX_CONNECTIONS,
            )
            try:
                await websocket.close(code=1008, reason="Too many connections")
            except Exception as exc:
                logger.error("Error closing rejected WebSocket: %s", exc)
            return

        # Track connection
        if service_name not in self._active_connections:
            self._active_connections[service_name] = set()
        self._active_connections[service_name].add(websocket)

        log_path = self._log_path(service_name)
        start_time = time.monotonic()
        timed_out = False

        try:
            # Send existing log content first
            if log_path.exists():
                try:
                    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                        if content:
                            await self._safe_send(websocket, content)
                except FileNotFoundError:
                    pass

            # Ensure log file exists for tailing
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.touch(exist_ok=True)

            # Then stream new content
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)  # Seek to end

                while True:
                    # Enforce maximum connection duration
                    if time.monotonic() - start_time >= self.MAX_CONNECTION_DURATION:
                        timed_out = True
                        logger.info(
                            "WebSocket for %s reached max duration, closing",
                            service_name,
                        )
                        break

                    line = f.readline()
                    if line:
                        await self._safe_send(websocket, line.rstrip("\n"))
                    else:
                        # 使用短超时的等待，可以快速响应关闭
                        if self._shutdown:
                            break
                        await asyncio.sleep(self.POLL_INTERVAL)

        except WebSocketDisconnect:
            # Client disconnected normally; nothing to log loudly
            pass
        except asyncio.TimeoutError:
            logger.warning(
                "WebSocket send timed out for %s after %.1fs; closing connection",
                service_name,
                self.SEND_TIMEOUT,
            )
        except asyncio.CancelledError:
            # Allow task cancellation to propagate after cleanup
            raise
        except Exception as exc:
            logger.error(
                "Unexpected error in log stream for %s: %s",
                service_name,
                exc,
                exc_info=True,
            )
        finally:
            # Always clean up connection tracking
            conns = self._active_connections.get(service_name)
            if conns is not None:
                conns.discard(websocket)
                if not conns:
                    self._active_connections.pop(service_name, None)

            # Attempt graceful close (no-op if already closed)
            try:
                if timed_out:
                    await websocket.close(code=1000, reason="Max duration reached")
                else:
                    await websocket.close()
            except Exception:
                # WebSocket may already be closed; ignore
                pass

    def has_connections(self, service_name: str) -> bool:
        return bool(self._active_connections.get(service_name))

    def shutdown(self):
        """Signal all streaming loops to stop."""
        self._shutdown = True


# Global instance
log_streamer = LogStreamer()
