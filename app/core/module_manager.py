"""Module lifecycle management: CRUD, file management, service binding."""
import json
import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import MODULES_DIR, SERVICES_DIR
from app.models.module import Module
from app.models.service import Service
from app.models.service_module import ServiceModule
from app.utils.validation import validate_service_name, validate_python_code, validate_toml_content
from app.core.module_loader import validate_module_code, get_module_template


class ModuleManagerError(Exception):
    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail


class ModuleNotFoundError(ModuleManagerError):
    pass


class ModuleManager:
    """Manages module CRUD, file operations, and service bindings."""

    async def create(
        self,
        session: AsyncSession,
        name: str,
        display_name: str,
        description: str | None = None,
        version: str = "1.0.0",
        author: str | None = None,
        code_source: str = "editor",
        code: str | None = None,
        config_toml: str | None = None,
    ) -> Module:
        """Create a new module."""
        validate_service_name(name)

        # Check uniqueness
        existing = await session.execute(select(Module).where(Module.name == name))
        if existing.scalar_one_or_none():
            raise ModuleManagerError(f"Module '{name}' already exists")

        # Default code
        if code is None:
            code = get_module_template().replace("my-module", name)

        # Validate module code
        valid, errors, module_info = validate_module_code(code)
        if not valid:
            raise ModuleManagerError("Invalid module code", "; ".join(errors))

        # Create module directory
        module_dir = MODULES_DIR / name
        module_dir.mkdir(parents=True, exist_ok=True)

        # Write module.py
        script_path = module_dir / "module.py"
        script_path.write_text(code, encoding="utf-8")

        # Write config.toml if provided
        config_path = None
        if config_toml:
            errors = validate_toml_content(config_toml)
            if errors:
                raise ModuleManagerError("Invalid TOML config", "; ".join(errors))
            config_path = module_dir / "config.toml"
            config_path.write_text(config_toml, encoding="utf-8")

        # Create database record
        module = Module(
            name=name,
            display_name=display_name,
            description=description,
            version=module_info.get("version", version) if module_info else version,
            author=author,
            code_source=code_source,
            script_path=str(script_path),
            config_path=str(config_path) if config_path else None,
        )
        session.add(module)
        await session.commit()
        await session.refresh(module)
        return module

    async def update(
        self,
        session: AsyncSession,
        name: str,
        display_name: str | None = None,
        description: str | None = None,
        version: str | None = None,
        author: str | None = None,
    ) -> Module:
        """Update module metadata."""
        module = await self._get_module(session, name)
        if display_name is not None:
            module.display_name = display_name
        if description is not None:
            module.description = description
        if version is not None:
            module.version = version
        if author is not None:
            module.author = author
        module.updated_at = datetime.now()
        await session.commit()
        await session.refresh(module)
        return module

    async def delete(self, session: AsyncSession, name: str) -> None:
        """Delete a module and its files."""
        module = await self._get_module(session, name)

        # Remove module directory
        module_dir = MODULES_DIR / name
        if module_dir.exists():
            shutil.rmtree(module_dir)

        # Delete DB record (cascades service_modules)
        await session.delete(module)
        await session.commit()

    async def get(self, session: AsyncSession, name: str) -> Module:
        return await self._get_module(session, name)

    async def list_all(self, session: AsyncSession) -> list[Module]:
        result = await session.execute(select(Module).order_by(Module.name))
        return list(result.scalars().all())

    async def get_service_count(self, session: AsyncSession, module_id: int) -> int:
        result = await session.execute(
            select(func.count(ServiceModule.id)).where(
                ServiceModule.module_id == module_id,
                ServiceModule.enabled == True,  # noqa: E712
            )
        )
        return result.scalar_one()

    async def get_services_using_module(self, session: AsyncSession, module_id: int) -> list[Service]:
        result = await session.execute(
            select(Service)
            .join(ServiceModule, Service.id == ServiceModule.service_id)
            .where(ServiceModule.module_id == module_id)
        )
        return list(result.scalars().all())

    async def update_code(
        self,
        session: AsyncSession,
        name: str,
        code: str,
        config_toml: str | None = None,
    ) -> Module:
        """Update module code and optional config."""
        module = await self._get_module(session, name)

        valid, errors, module_info = validate_module_code(code)
        if not valid:
            raise ModuleManagerError("Invalid module code", "; ".join(errors))

        script_path = Path(module.script_path)
        script_path.write_text(code, encoding="utf-8")

        if config_toml is not None:
            errors = validate_toml_content(config_toml)
            if errors:
                raise ModuleManagerError("Invalid TOML config", "; ".join(errors))
            config_path = Path(module.config_path) if module.config_path else MODULES_DIR / name / "config.toml"
            config_path.write_text(config_toml, encoding="utf-8")
            module.config_path = str(config_path)

        module.updated_at = datetime.now()
        await session.commit()
        await session.refresh(module)
        return module

    async def get_code(self, session: AsyncSession, name: str) -> dict:
        """Get module code and config content."""
        module = await self._get_module(session, name)
        script_path = Path(module.script_path)
        code = script_path.read_text(encoding="utf-8") if script_path.exists() else ""

        config_toml = None
        if module.config_path:
            config_path = Path(module.config_path)
            if config_path.exists():
                config_toml = config_path.read_text(encoding="utf-8")

        return {"code": code, "config_toml": config_toml}

    # ── Service-Module Binding ─────────────────────────────────

    async def get_service_modules(self, session: AsyncSession, service_name: str) -> list[dict]:
        """Get all modules with their enabled status for a service."""
        service_result = await session.execute(select(Service).where(Service.name == service_name))
        service = service_result.scalar_one_or_none()
        if service is None:
            raise ModuleManagerError(f"Service '{service_name}' not found")

        # Get all modules
        all_modules = await self.list_all(session)

        # Get existing bindings
        bindings_result = await session.execute(
            select(ServiceModule).where(ServiceModule.service_id == service.id)
        )
        bindings = {sm.module_id: sm for sm in bindings_result.scalars().all()}

        result = []
        for mod in all_modules:
            binding = bindings.get(mod.id)
            result.append({
                "module_id": mod.id,
                "name": mod.name,
                "display_name": mod.display_name,
                "version": mod.version,
                "description": mod.description,
                "enabled": binding.enabled if binding else False,
                "load_order": binding.load_order if binding else 0,
            })

        return result

    async def update_service_modules(
        self,
        session: AsyncSession,
        service_name: str,
        modules_list: list[dict],
    ) -> dict:
        """Batch update service-module bindings.
        modules_list: [{module_id, enabled, load_order}]"""
        service_result = await session.execute(select(Service).where(Service.name == service_name))
        service = service_result.scalar_one_or_none()
        if service is None:
            raise ModuleManagerError(f"Service '{service_name}' not found")

        # Get existing bindings
        bindings_result = await session.execute(
            select(ServiceModule).where(ServiceModule.service_id == service.id)
        )
        existing = {sm.module_id: sm for sm in bindings_result.scalars().all()}

        changed = []
        for item in modules_list:
            module_id = item["module_id"]
            enabled = item.get("enabled", True)
            load_order = item.get("load_order", 0)

            if module_id in existing:
                binding = existing[module_id]
                if binding.enabled != enabled or binding.load_order != load_order:
                    binding.enabled = enabled
                    binding.load_order = load_order
                    changed.append(module_id)
            else:
                new_binding = ServiceModule(
                    service_id=service.id,
                    module_id=module_id,
                    enabled=enabled,
                    load_order=load_order,
                )
                session.add(new_binding)
                changed.append(module_id)

        await session.commit()

        # Write modules registry file for runner
        await self._write_modules_registry(session, service)

        return {
            "restart_required": len(changed) > 0,
            "changed_count": len(changed),
        }

    async def _write_modules_registry(self, session: AsyncSession, service: Service) -> None:
        """Write .modules.json for runner to read at startup."""
        bindings_result = await session.execute(
            select(ServiceModule).where(
                ServiceModule.service_id == service.id,
                ServiceModule.enabled == True,  # noqa: E712
            ).order_by(ServiceModule.load_order)
        )
        bindings = bindings_result.scalars().all()

        registry = []
        for binding in bindings:
            mod_result = await session.execute(select(Module).where(Module.id == binding.module_id))
            mod = mod_result.scalar_one_or_none()
            if mod:
                registry.append({
                    "name": mod.name,
                    "script_path": mod.script_path,
                    "config_path": mod.config_path or "",
                })

        registry_path = SERVICES_DIR / service.name / ".modules.json"
        registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")

    async def _get_module(self, session: AsyncSession, name: str) -> Module:
        result = await session.execute(select(Module).where(Module.name == name))
        mod = result.scalar_one_or_none()
        if mod is None:
            raise ModuleNotFoundError(f"Module '{name}' not found")
        return mod
