"""Log streaming via WebSocket and history retrieval."""
import asyncio
from pathlib import Path
from typing import Set

from fastapi import WebSocket, WebSocketDisconnect

from app.config import SERVICES_DIR


class LogStreamer:
    """Reads service log files and streams them via WebSocket."""

    def __init__(self):
        self._active_connections: dict[str, Set[WebSocket]] = {}

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
        except Exception:
            return []

    async def stream_to_websocket(self, service_name: str, websocket: WebSocket):
        """Stream log updates to a WebSocket client."""
        await websocket.accept()
        log_path = self._log_path(service_name)

        # Track connection
        if service_name not in self._active_connections:
            self._active_connections[service_name] = set()
        self._active_connections[service_name].add(websocket)

        try:
            # Send existing log content first
            if log_path.exists():
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                    if content:
                        await websocket.send_text(content)

            # Then stream new content
            with open(log_path, "a", encoding="utf-8", errors="replace") as f_write:
                pass  # Ensure file exists

            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)  # Seek to end

                while True:
                    line = f.readline()
                    if line:
                        await websocket.send_text(line.rstrip("\n"))
                    else:
                        await asyncio.sleep(0.2)

        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            self._active_connections[service_name].discard(websocket)
            if not self._active_connections[service_name]:
                del self._active_connections[service_name]

    def has_connections(self, service_name: str) -> bool:
        return bool(self._active_connections.get(service_name))


# Global instance
log_streamer = LogStreamer()
