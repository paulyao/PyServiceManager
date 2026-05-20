"""Watchdog-based config file watcher for hot-reload."""
import asyncio
import os
import signal
import threading
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileModifiedEvent

from app.config import SERVICES_DIR
from app.utils.system import get_service_pid_from_file


class ConfigEventHandler(FileSystemEventHandler):
    """Handles config.toml file change events with debouncing."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        self._debounce_timers: dict[str, threading.Timer] = {}

    def on_modified(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.name != "config.toml":
            return

        # Extract service name from path: data/services/{name}/config.toml
        try:
            service_name = path.parent.name
        except (ValueError, IndexError):
            return

        # Debounce: 500ms
        if service_name in self._debounce_timers:
            self._debounce_timers[service_name].cancel()

        self._debounce_timers[service_name] = threading.Timer(
            0.5, self._trigger_reload, args=[service_name]
        )
        self._debounce_timers[service_name].start()

    def _trigger_reload(self, service_name: str):
        """Send SIGHUP to the running service process."""
        pid_path = SERVICES_DIR / service_name / ".pid"
        pid = get_service_pid_from_file(str(pid_path))
        if pid:
            try:
                os.kill(pid, signal.SIGHUP)
            except ProcessLookupError:
                pass
        self._debounce_timers.pop(service_name, None)


class ConfigWatcher:
    """Monitors service config.toml files for hot-reload."""

    def __init__(self):
        self._observer: Observer | None = None
        self._handler: ConfigEventHandler | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self):
        """Start watching config files."""
        self._loop = asyncio.get_event_loop()
        self._handler = ConfigEventHandler(self._loop)
        self._observer = Observer()

        if SERVICES_DIR.exists():
            self._observer.schedule(self._handler, str(SERVICES_DIR), recursive=True)
        else:
            SERVICES_DIR.mkdir(parents=True, exist_ok=True)
            self._observer.schedule(self._handler, str(SERVICES_DIR), recursive=True)

        self._observer.daemon = True
        self._observer.start()

    async def stop(self):
        """Stop watching."""
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
