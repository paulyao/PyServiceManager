"""Service lifecycle management with dual backend support."""
import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import MODULES_DIR, SERVICES_DIR, TEMPLATES_DIR, DEFAULT_PYTHON_PATH
from app.models.module import Module
from app.models.service import Service
from app.models.service_module import ServiceModule
from app.utils.system import has_systemctl, systemctl, run_command, CommandResult, get_service_pid_from_file
from app.utils.validation import validate_service_name, validate_python_code, validate_toml_content

logger = logging.getLogger(__name__)


class ServiceManagerError(Exception):
    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail


class ServiceNotFoundError(ServiceManagerError):
    pass


class ServiceCommandError(ServiceManagerError):
    pass


class ServiceBackend:
    """Abstract backend interface."""

    async def start(self, service: Service) -> dict:
        raise NotImplementedError

    async def stop(self, service: Service) -> dict:
        raise NotImplementedError

    async def restart(self, service: Service) -> dict:
        raise NotImplementedError

    async def enable(self, service: Service) -> None:
        raise NotImplementedError

    async def disable(self, service: Service) -> None:
        raise NotImplementedError

    async def get_status(self, service: Service) -> dict:
        raise NotImplementedError

    async def install_unit(self, service: Service) -> None:
        raise NotImplementedError

    async def uninstall_unit(self, service: Service) -> None:
        raise NotImplementedError


class SystemdBackend(ServiceBackend):
    """Linux systemd backend."""

    async def start(self, service: Service) -> dict:
        # If python_path is stale, regenerate unit file with current Python
        if service.python_path and not Path(service.python_path).exists():
            new_python_path = DEFAULT_PYTHON_PATH
            logger.warning(
                "Service '%s' python_path '%s' not found, regenerating unit file with '%s'",
                service.name, service.python_path, new_python_path,
            )
            from app.core.runner_template import generate_unit
            generate_unit(
                service.name,
                service.display_name or service.name,
                SERVICES_DIR / service.name,
                new_python_path,
                service.auto_restart,
            )
            # Re-install the updated unit file
            await self.install_unit(service)

        result = await systemctl("start", service.name)
        if not result.ok:
            raise ServiceCommandError(f"Failed to start {service.name}", result.stderr)
        return await self.get_status(service)

    async def stop(self, service: Service) -> dict:
        result = await systemctl("stop", service.name)
        if not result.ok:
            raise ServiceCommandError(f"Failed to stop {service.name}", result.stderr)
        return await self.get_status(service)

    async def restart(self, service: Service) -> dict:
        result = await systemctl("restart", service.name)
        if not result.ok:
            raise ServiceCommandError(f"Failed to restart {service.name}", result.stderr)
        return await self.get_status(service)

    async def enable(self, service: Service) -> None:
        result = await systemctl("enable", service.name)
        if not result.ok:
            raise ServiceCommandError(f"Failed to enable {service.name}", result.stderr)

    async def disable(self, service: Service) -> None:
        result = await systemctl("disable", service.name)
        if not result.ok:
            raise ServiceCommandError(f"Failed to disable {service.name}", result.stderr)

    async def get_status(self, service: Service) -> dict:
        active_result = await systemctl("is-active", service.name)
        enabled_result = await systemctl("is-enabled", service.name)
        show_result = await run_command(
            ["systemctl", "show", f"{service.name}.service", "--property=MainPID"],
        )
        pid = None
        if show_result.ok and "MainPID=" in show_result.stdout:
            try:
                pid = int(show_result.stdout.split("MainPID=")[1].strip())
                if pid == 0:
                    pid = None
            except (ValueError, IndexError):
                pass

        return {
            "active": active_result.stdout if active_result.returncode == 0 else "inactive",
            "enabled": enabled_result.stdout == "enabled",
            "pid": pid,
        }

    async def install_unit(self, service: Service) -> None:
        unit_src = SERVICES_DIR / service.name / f"{service.name}.service"
        unit_dst = Path(f"/etc/systemd/system/{service.name}.service")
        try:
            shutil.copy2(str(unit_src), str(unit_dst))
        except PermissionError:
            result = await run_command(["sudo", "cp", str(unit_src), str(unit_dst)])
            if not result.ok:
                raise ServiceCommandError("Failed to install unit file", result.stderr)

        result = await run_command(["systemctl", "daemon-reload"])
        if not result.ok:
            raise ServiceCommandError("daemon-reload failed", result.stderr)

    async def uninstall_unit(self, service: Service) -> None:
        unit_dst = Path(f"/etc/systemd/system/{service.name}.service")
        if unit_dst.exists():
            try:
                unit_dst.unlink()
            except PermissionError:
                await run_command(["sudo", "rm", str(unit_dst)])
            await run_command(["systemctl", "daemon-reload"])


class ProcessBackend(ServiceBackend):
    """macOS / development mode: subprocess-based process management."""

    def _pid_path(self, service: Service) -> Path:
        return SERVICES_DIR / service.name / ".pid"

    def _log_path(self, service: Service) -> Path:
        return SERVICES_DIR / service.name / "runner.log"

    def _runner_path(self, service: Service) -> Path:
        return SERVICES_DIR / service.name / "runner.py"

    def _get_process(self, service: Service) -> subprocess.Popen | None:
        pid = get_service_pid_from_file(str(self._pid_path(service)))
        if pid is None:
            return None
        try:
            os.kill(pid, 0)  # Check if process is alive
            return pid
        except ProcessLookupError:
            # Clean up stale pid file
            pid_path = self._pid_path(service)
            if pid_path.exists():
                pid_path.unlink()
            return None

    async def start(self, service: Service) -> dict:
        pid = self._get_process(service)
        if pid is not None:
            return await self.get_status(service)

        # Auto-fix python_path: if stored path doesn't exist, fall back to current Python
        python_path = service.python_path
        if not python_path or not Path(python_path).exists():
            python_path = DEFAULT_PYTHON_PATH
            logger.warning(
                "Service '%s' python_path '%s' not found, falling back to '%s'",
                service.name, service.python_path, python_path,
            )

        if not Path(service.working_dir).exists():
            raise ServiceManagerError(
                f"Service '{service.name}' working directory not found: {service.working_dir}"
            )

        log_file = open(self._log_path(service), "a")
        try:
            proc = subprocess.Popen(
                [python_path, "-u", str(self._runner_path(service))],
                stdout=log_file,
                stderr=log_file,
                cwd=service.working_dir,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
        except FileNotFoundError as e:
            raise ServiceManagerError(
                f"Failed to start service '{service.name}': Python interpreter not found: {e}"
            )
        finally:
            # Close fd in parent process; child has already inherited its own fd copy
            log_file.close()
        # Write PID file
        self._pid_path(service).write_text(str(proc.pid))
        return {"active": "active", "enabled": False, "pid": proc.pid}

    async def stop(self, service: Service) -> dict:
        pid = self._get_process(service)
        if pid is None:
            return {"active": "inactive", "enabled": False, "pid": None}

        try:
            os.kill(pid, signal.SIGTERM)
            # Wait for process to exit
            for _ in range(50):  # 5 seconds
                await asyncio.sleep(0.1)
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
            else:
                os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

        pid_path = self._pid_path(service)
        if pid_path.exists():
            pid_path.unlink()

        return {"active": "inactive", "enabled": False, "pid": None}

    async def restart(self, service: Service) -> dict:
        await self.stop(service)
        await asyncio.sleep(0.5)
        return await self.start(service)

    async def enable(self, service: Service) -> None:
        pass  # Not supported in process mode

    async def disable(self, service: Service) -> None:
        pass  # Not supported in process mode

    async def get_status(self, service: Service) -> dict:
        pid = self._get_process(service)
        return {
            "active": "active" if pid is not None else "inactive",
            "enabled": False,
            "pid": pid,
        }

    async def install_unit(self, service: Service) -> None:
        pass  # No unit file needed in process mode

    async def uninstall_unit(self, service: Service) -> None:
        pass


class ServiceManager:
    """Manages service lifecycle using the appropriate backend."""

    def __init__(self):
        if has_systemctl():
            self._backend: ServiceBackend = SystemdBackend()
        else:
            self._backend = ProcessBackend()

    @property
    def backend_name(self) -> str:
        return type(self._backend).__name__

    @staticmethod
    def _resolve_python_path(python_path: str) -> str:
        """Resolve python_path to an actual executable."""
        if python_path == "auto" or not python_path:
            return DEFAULT_PYTHON_PATH
        # Validate that the explicitly provided path exists
        if not Path(python_path).exists():
            raise ServiceManagerError(f"Python interpreter not found: {python_path}")
        return python_path

    async def create(
        self,
        session: AsyncSession,
        name: str,
        display_name: str | None = None,
        description: str | None = None,
        code_source: str = "editor",
        code: str | None = None,
        python_path: str = "auto",
        auto_restart: bool = True,
    ) -> Service:
        """Create a new service with all required files."""
        validate_service_name(name)

        # Resolve python path
        resolved_python = self._resolve_python_path(python_path)

        # Check uniqueness
        existing = await session.execute(select(Service).where(Service.name == name))
        if existing.scalar_one_or_none():
            raise ServiceManagerError(f"Service '{name}' already exists")

        # Default code template
        if code is None:
            code = _default_service_code(name)

        # Validate code
        errors = validate_python_code(code)
        if errors:
            raise ServiceManagerError("Invalid Python code", "; ".join(errors))

        # Create service directory
        service_dir = SERVICES_DIR / name
        service_dir.mkdir(parents=True, exist_ok=True)

        # Write main.py
        main_path = service_dir / "main.py"
        main_path.write_text(code, encoding="utf-8")

        # Write default config.toml
        config_path = service_dir / "config.toml"
        default_config = f"# Configuration for service: {name}\n[interval]\nseconds = 5\n\n[message]\ntext = \"Hello from {name}\"\n"
        config_path.write_text(default_config, encoding="utf-8")

        # Generate runner.py
        from app.core.runner_template import generate_runner
        generate_runner(name, service_dir, resolved_python)

        # Generate systemd unit (for Linux)
        from app.core.runner_template import generate_unit
        unit_path = generate_unit(name, display_name or name, service_dir, resolved_python, auto_restart)

        # Create database record
        service = Service(
            name=name,
            display_name=display_name or name,
            description=description,
            code_source=code_source,
            script_path=str(main_path.relative_to(SERVICES_DIR.parent)),
            config_path=str(config_path.relative_to(SERVICES_DIR.parent)),
            unit_path=str(unit_path) if unit_path else None,
            status="stopped",
            enabled=False,
            auto_restart=auto_restart,
            python_path=resolved_python,
            working_dir=str(service_dir),
        )
        session.add(service)
        try:
            await session.commit()
            await session.refresh(service)
        except IntegrityError as e:
            await session.rollback()
            raise ServiceManagerError(f"Service name conflict: {e}")
        except Exception as e:
            await session.rollback()
            raise ServiceManagerError(f"Database error while creating service: {e}")

        # Install unit file (systemd only)
        try:
            await self._backend.install_unit(service)
        except ServiceCommandError:
            # Non-fatal: unit install may fail without root
            pass

        return service

    async def delete(self, session: AsyncSession, name: str) -> None:
        """Delete a service and all its files."""
        service = await self._get_service(session, name)

        # Stop if running
        if service.status == "running":
            await self.stop(session, name)

        # Uninstall unit
        try:
            await self._backend.uninstall_unit(service)
        except Exception:
            pass

        # Remove service directory
        service_dir = SERVICES_DIR / name
        if service_dir.exists():
            shutil.rmtree(service_dir)

        # Remove database record (cascades service_modules)
        await session.delete(service)
        try:
            await session.commit()
        except Exception as e:
            await session.rollback()
            raise ServiceManagerError(f"Database error while deleting service: {e}")

    async def start(self, session: AsyncSession, name: str) -> Service:
        service = await self._get_service(session, name)

        # Detect stale paths and track whether files need regeneration
        needs_regenerate = False
        new_python_path = service.python_path

        # Fix stale python_path: update DB and regenerate files
        if service.python_path and not Path(service.python_path).exists():
            new_python_path = DEFAULT_PYTHON_PATH
            logger.warning(
                "Service '%s' has stale python_path '%s', updating to '%s'",
                service.name, service.python_path, new_python_path,
            )
            service.python_path = new_python_path
            needs_regenerate = True

        # Fix stale working_dir: always use current SERVICES_DIR layout
        expected_working_dir = str(SERVICES_DIR / service.name)
        if service.working_dir != expected_working_dir:
            logger.warning(
                "Service '%s' has stale working_dir '%s', updating to '%s'",
                service.name, service.working_dir, expected_working_dir,
            )
            service.working_dir = expected_working_dir
            needs_regenerate = True

        # Regenerate service files (runner.py + unit) and reinstall unit if needed
        if needs_regenerate:
            self._regenerate_service_files(service, new_python_path)
            if isinstance(self._backend, SystemdBackend):
                await self._backend.install_unit(service)

        # Fix stale module paths and rewrite .modules.json before starting
        await self._fix_stale_module_paths(session, service)

        status = await self._backend.start(service)
        service.status = status["active"] if status["active"] in ("active", "running") else "failed"
        if service.status in ("active", "running"):
            service.status = "running"
            service.started_at = datetime.now()
        try:
            await session.commit()
            await session.refresh(service)
        except Exception as e:
            await session.rollback()
            raise ServiceManagerError(f"Database error while updating service status: {e}")
        return service

    async def stop(self, session: AsyncSession, name: str) -> Service:
        service = await self._get_service(session, name)
        status = await self._backend.stop(service)
        service.status = "stopped"
        try:
            await session.commit()
            await session.refresh(service)
        except Exception as e:
            await session.rollback()
            raise ServiceManagerError(f"Database error while updating service status: {e}")
        return service

    async def restart(self, session: AsyncSession, name: str) -> Service:
        service = await self._get_service(session, name)
        status = await self._backend.restart(service)
        service.status = "running" if status["active"] in ("active", "running") else "failed"
        if service.status == "running":
            service.started_at = datetime.now()
        try:
            await session.commit()
            await session.refresh(service)
        except Exception as e:
            await session.rollback()
            raise ServiceManagerError(f"Database error while updating service status: {e}")
        return service

    async def enable(self, session: AsyncSession, name: str) -> Service:
        service = await self._get_service(session, name)
        await self._backend.enable(service)
        service.enabled = True
        try:
            await session.commit()
            await session.refresh(service)
        except Exception as e:
            await session.rollback()
            raise ServiceManagerError(f"Database error while enabling service: {e}")
        return service

    async def disable(self, session: AsyncSession, name: str) -> Service:
        service = await self._get_service(session, name)
        await self._backend.disable(service)
        service.enabled = False
        try:
            await session.commit()
            await session.refresh(service)
        except Exception as e:
            await session.rollback()
            raise ServiceManagerError(f"Database error while disabling service: {e}")
        return service

    async def get_status(self, session: AsyncSession, name: str) -> dict:
        service = await self._get_service(session, name)
        return await self._backend.get_status(service)

    async def sync_all_status(self, session: AsyncSession) -> None:
        """Sync all services' status from the backend."""
        result = await session.execute(select(Service))
        services = result.scalars().all()
        for svc in services:
            try:
                status = await self._backend.get_status(svc)
                new_status = "running" if status["active"] in ("active", "running") else "stopped"
                svc.status = new_status
            except Exception:
                svc.status = "unknown"
        try:
            await session.commit()
        except Exception as e:
            await session.rollback()
            raise ServiceManagerError(f"Database error while syncing service status: {e}")

    async def update_code(self, session: AsyncSession, name: str, code: str) -> Service:
        service = await self._get_service(session, name)
        errors = validate_python_code(code)
        if errors:
            raise ServiceManagerError("Invalid Python code", "; ".join(errors))

        main_path = SERVICES_DIR / name / "main.py"
        main_path.write_text(code, encoding="utf-8")
        service.updated_at = datetime.now()
        try:
            await session.commit()
            await session.refresh(service)
        except Exception as e:
            await session.rollback()
            raise ServiceManagerError(f"Database error while updating service code: {e}")
        return service

    async def get_code(self, session: AsyncSession, name: str) -> str:
        service = await self._get_service(session, name)
        main_path = SERVICES_DIR / name / "main.py"
        if main_path.exists():
            return main_path.read_text(encoding="utf-8")
        return ""

    async def update_config(self, session: AsyncSession, name: str, config: str) -> Service:
        service = await self._get_service(session, name)
        errors = validate_toml_content(config)
        if errors:
            raise ServiceManagerError("Invalid TOML config", "; ".join(errors))

        config_path = SERVICES_DIR / name / "config.toml"
        config_path.write_text(config, encoding="utf-8")

        # Signal running service to reload config
        if service.status == "running":
            await self._signal_reload(service)

        service.updated_at = datetime.now()
        try:
            await session.commit()
            await session.refresh(service)
        except Exception as e:
            await session.rollback()
            raise ServiceManagerError(f"Database error while updating service config: {e}")
        return service

    async def get_config(self, session: AsyncSession, name: str) -> str:
        service = await self._get_service(session, name)
        config_path = SERVICES_DIR / name / "config.toml"
        if config_path.exists():
            return config_path.read_text(encoding="utf-8")
        return ""

    async def _fix_stale_module_paths(self, session: AsyncSession, service: Service) -> None:
        """Fix stale module paths in DB and rewrite .modules.json."""
        bindings_result = await session.execute(
            select(ServiceModule).where(
                ServiceModule.service_id == service.id,
                ServiceModule.enabled == True,  # noqa: E712
            ).order_by(ServiceModule.load_order)
        )
        bindings = bindings_result.scalars().all()

        registry = []
        paths_updated = False
        for binding in bindings:
            mod_result = await session.execute(select(Module).where(Module.id == binding.module_id))
            mod = mod_result.scalar_one_or_none()
            if not mod:
                continue

            # Check and fix script_path
            expected_script = str(MODULES_DIR / mod.name / "module.py")
            expected_config = str(MODULES_DIR / mod.name / "config.toml")

            if mod.script_path != expected_script and not Path(mod.script_path).exists():
                logger.warning(
                    "Module '%s' has stale script_path '%s', updating to '%s'",
                    mod.name, mod.script_path, expected_script,
                )
                mod.script_path = expected_script
                paths_updated = True

            if mod.config_path and mod.config_path != expected_config and not Path(mod.config_path).exists():
                logger.warning(
                    "Module '%s' has stale config_path '%s', updating to '%s'",
                    mod.name, mod.config_path, expected_config,
                )
                mod.config_path = expected_config
                paths_updated = True

            registry.append({
                "name": mod.name,
                "script_path": mod.script_path,
                "config_path": mod.config_path or "",
            })

        if paths_updated:
            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.error("Failed to update module paths: %s", e)

        # Always rewrite .modules.json with current paths
        registry_path = SERVICES_DIR / service.name / ".modules.json"
        registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")

    def _regenerate_service_files(self, service: Service, new_python_path: str) -> None:
        """Regenerate runner.py and systemd unit file with an updated python_path."""
        service_dir = SERVICES_DIR / service.name
        if not service_dir.exists():
            return

        from app.core.runner_template import generate_runner, generate_unit
        generate_runner(service.name, service_dir, new_python_path)
        generate_unit(
            service.name,
            service.display_name or service.name,
            service_dir,
            new_python_path,
            service.auto_restart,
        )
        logger.info(
            "Regenerated service files for '%s' with python_path: %s",
            service.name, new_python_path,
        )

    async def _signal_reload(self, service: Service) -> None:
        """Send SIGHUP to a running service."""
        if isinstance(self._backend, ProcessBackend):
            pid = get_service_pid_from_file(str(SERVICES_DIR / service.name / ".pid"))
            if pid:
                try:
                    os.kill(pid, signal.SIGHUP)
                    logger.info("Sent SIGHUP to service '%s' (PID: %s)", service.name, pid)
                except ProcessLookupError:
                    logger.warning("Service '%s' process (PID: %s) not found", service.name, pid)
            else:
                logger.warning("No PID file found for service '%s', cannot send reload signal", service.name)
        else:
            result = await run_command(["systemctl", "reload", f"{service.name}.service"])
            if result.ok:
                logger.info("Reloaded service '%s' via systemctl", service.name)
            else:
                logger.error("Failed to reload service '%s': %s", service.name, result.stderr)

    async def _get_service(self, session: AsyncSession, name: str) -> Service:
        result = await session.execute(select(Service).where(Service.name == name))
        svc = result.scalar_one_or_none()
        if svc is None:
            raise ServiceNotFoundError(f"Service '{name}' not found")
        return svc


def _default_service_code(name: str) -> str:
    return f'''"""Service: {name}"""
import time


def run(config, modules):
    """Main entry point.
    
    Args:
        config: dict from config.toml
        modules: dict of enabled module namespaces, e.g. modules["module-name"].method()
    """
    interval = config.get("interval", {{}}).get("seconds", 5)
    message = config.get("message", {{}}).get("text", "Hello from {name}")

    while True:
        print(f"[{name}] {{message}}")
        time.sleep(interval)


def on_config_reload(new_config):
    """Called when config.toml is hot-reloaded."""
    print(f"[{name}] Config reloaded")


def on_shutdown():
    """Called when service is stopping."""
    print(f"[{name}] Shutting down")
'''
