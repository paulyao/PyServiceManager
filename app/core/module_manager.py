"""Module lifecycle management: CRUD, file management, service binding."""
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import MODULES_DIR, SERVICES_DIR, BUILTIN_MODULES_DIR, DATA_DIR
from app.models.module import Module
from app.models.service import Service
from app.models.service_module import ServiceModule
from app.utils.validation import validate_service_name, validate_python_code, validate_toml_content, validate_module_code
from app.core.module_loader import get_module_template

logger = logging.getLogger(__name__)


class ModuleManagerError(Exception):
    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail


class ModuleNotFoundError(ModuleManagerError):
    pass


class BuiltinModuleError(ModuleManagerError):
    """Raised when trying to delete a built-in module."""
    pass


class ModuleManager:
    """Manages module CRUD, file operations, and service bindings."""

    BUILTIN_REQUIREMENTS: dict[str, list[str]] = {
        "mysql-helper": ["pymysql>=1.1"],
        "http-client": ["certifi"],
        "idaas-eiam": ["alibabacloud-eiam20211201>=2.0"],
    }

    @staticmethod
    def _relative_path(abs_path: Path | str) -> str:
        """Convert absolute path to relative path from DATA_DIR parent."""
        p = Path(abs_path)
        try:
            return str(p.relative_to(DATA_DIR.parent))
        except ValueError:
            # If path is already relative or not under DATA_DIR, store as-is
            return str(p)

    @staticmethod
    def _resolve_path(stored_path: str) -> Path:
        """Resolve a stored path (relative or absolute) to an absolute Path.
        Relative paths are resolved from DATA_DIR parent (project root)."""
        p = Path(stored_path)
        if p.is_absolute():
            return p
        return DATA_DIR.parent / stored_path

    async def ensure_builtin_modules(self, session: AsyncSession) -> list[str]:
        """Ensure all built-in modules from builtin_modules/ are registered.
        Returns list of newly created module names."""
        if not BUILTIN_MODULES_DIR.exists():
            return []

        created = []
        for module_dir in sorted(BUILTIN_MODULES_DIR.iterdir()):
            if not module_dir.is_dir():
                continue

            script_file = module_dir / "module.py"
            if not script_file.exists():
                continue

            name = module_dir.name

            # Check if already registered (by name or by builtin_source for renamed modules)
            existing_by_name = await session.execute(select(Module).where(Module.name == name))
            if existing_by_name.scalar_one_or_none():
                continue
            existing_by_source = await session.execute(
                select(Module).where(Module.builtin_source == name, Module.is_builtin == True)  # noqa: E712
            )
            if existing_by_source.scalar_one_or_none():
                continue

            # Read code and optional config
            code = script_file.read_text(encoding="utf-8")
            config_file = module_dir / "config.toml"
            config_toml = config_file.read_text(encoding="utf-8") if config_file.exists() else None

            # Validate
            valid, errors, module_info = validate_module_code(code)
            if not valid:
                continue

            # Create module directory in data/
            target_dir = MODULES_DIR / name
            target_dir.mkdir(parents=True, exist_ok=True)

            # Copy script
            target_script = target_dir / "module.py"
            target_script.write_text(code, encoding="utf-8")

            # Copy config if present
            target_config = None
            if config_toml:
                target_config = target_dir / "config.toml"
                target_config.write_text(config_toml, encoding="utf-8")

            # Create DB record with is_builtin=True
            module = Module(
                name=name,
                display_name=module_info.get("description", name) if module_info else name,
                description=module_info.get("description") if module_info else None,
                version=module_info.get("version", "1.0.0") if module_info else "1.0.0",
                code_source="builtin",
                script_path=self._relative_path(target_script),
                config_path=self._relative_path(target_config) if target_config else None,
                is_builtin=True,
                builtin_source=name,
                requirements=json.dumps(self.BUILTIN_REQUIREMENTS.get(name, [])),
            )
            session.add(module)
            created.append(name)

        if created:
            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise ModuleManagerError(f"Database error while registering builtin modules: {e}")

        return created

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
            script_path=self._relative_path(script_path),
            config_path=self._relative_path(config_path) if config_path else None,
        )
        session.add(module)
        try:
            await session.commit()
            await session.refresh(module)
        except IntegrityError as e:
            await session.rollback()
            raise ModuleManagerError(f"Module name conflict: {e}")
        except Exception as e:
            await session.rollback()
            raise ModuleManagerError(f"Database error while creating module: {e}")
        return module

    async def update(
        self,
        session: AsyncSession,
        name: str,
        new_name: str | None = None,
        display_name: str | None = None,
        description: str | None = None,
        version: str | None = None,
        author: str | None = None,
    ) -> Module:
        """Update module metadata. Supports renaming via new_name."""
        module = await self._get_module(session, name)

        # Handle rename
        if new_name is not None and new_name != name:
            validate_service_name(new_name)

            # Check uniqueness of new name
            existing = await session.execute(select(Module).where(Module.name == new_name))
            if existing.scalar_one_or_none():
                raise ModuleManagerError(f"Module '{new_name}' already exists")

            old_dir = MODULES_DIR / name
            new_dir = MODULES_DIR / new_name

            # Rename directory on disk
            if old_dir.exists():
                old_dir.rename(new_dir)

            # Update script_path and config_path
            old_script = Path(module.script_path)
            new_script = new_dir / old_script.name
            module.script_path = self._relative_path(new_script)

            if module.config_path:
                old_config = Path(module.config_path)
                new_config = new_dir / old_config.name
                module.config_path = self._relative_path(new_config)

            # For built-in modules, set builtin_source if not already set
            if module.is_builtin and not module.builtin_source:
                module.builtin_source = name

            module.name = new_name

            # Update .modules.json for all bound services
            bindings = await session.execute(
                select(ServiceModule).where(ServiceModule.module_id == module.id)
            )
            bound_services = bindings.scalars().all()
            for sm in bound_services:
                svc_result = await session.execute(select(Service).where(Service.id == sm.service_id))
                svc = svc_result.scalar_one_or_none()
                if svc:
                    await self._write_modules_registry(session, svc)

        if display_name is not None:
            module.display_name = display_name
        if description is not None:
            module.description = description
        if version is not None:
            module.version = version
        if author is not None:
            module.author = author
        module.updated_at = datetime.now()
        try:
            await session.commit()
            await session.refresh(module)
        except IntegrityError as e:
            await session.rollback()
            raise ModuleManagerError(f"Module name conflict: {e}")
        except Exception as e:
            await session.rollback()
            raise ModuleManagerError(f"Database error while updating module: {e}")
        return module

    async def delete(self, session: AsyncSession, name: str) -> None:
        """Delete a module and its files. Built-in modules cannot be deleted."""
        module = await self._get_module(session, name)

        if module.is_builtin:
            raise BuiltinModuleError(f"Built-in module '{name}' cannot be deleted")

        # Remove module directory
        module_dir = MODULES_DIR / name
        if module_dir.exists():
            shutil.rmtree(module_dir)

        # Delete DB record (cascades service_modules)
        await session.delete(module)
        try:
            await session.commit()
        except Exception as e:
            await session.rollback()
            raise ModuleManagerError(f"Database error while deleting module: {e}")

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

        script_path = self._resolve_path(module.script_path)
        script_path.write_text(code, encoding="utf-8")

        if config_toml is not None:
            errors = validate_toml_content(config_toml)
            if errors:
                raise ModuleManagerError("Invalid TOML config", "; ".join(errors))
            config_path = self._resolve_path(module.config_path) if module.config_path else MODULES_DIR / name / "config.toml"
            config_path.write_text(config_toml, encoding="utf-8")
            module.config_path = self._relative_path(config_path)

        module.updated_at = datetime.now()
        try:
            await session.commit()
            await session.refresh(module)
        except Exception as e:
            await session.rollback()
            raise ModuleManagerError(f"Database error while updating module code: {e}")
        return module

    async def get_code(self, session: AsyncSession, name: str) -> dict:
        """Get module code and config content."""
        module = await self._get_module(session, name)
        script_path = self._resolve_path(module.script_path)
        code = script_path.read_text(encoding="utf-8") if script_path.exists() else ""

        config_toml = None
        if module.config_path:
            config_path = self._resolve_path(module.config_path)
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

        try:
            await session.commit()
        except Exception as e:
            await session.rollback()
            raise ModuleManagerError(f"Database error while updating service module bindings: {e}")

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
                    "script_path": str(self._resolve_path(mod.script_path)),
                    "config_path": str(self._resolve_path(mod.config_path)) if mod.config_path else "",
                })

        registry_path = SERVICES_DIR / service.name / ".modules.json"
        registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")

    async def repair_paths(self, session: AsyncSession) -> int:
        """Repair module script_path and config_path stored as absolute paths.
        Converts them to relative paths, resolves stale/deploy-mismatched paths,
        and restores builtin module files if missing on disk.
        Returns number of modules repaired."""
        modules = await self.list_all(session)
        repaired = 0
        for mod in modules:
            expected_script = self._relative_path(MODULES_DIR / mod.name / "module.py")
            expected_config = self._relative_path(MODULES_DIR / mod.name / "config.toml")

            script_path = self._resolve_path(mod.script_path)

            # Restore builtin module files from builtin_modules/ if missing
            if not script_path.exists() and mod.is_builtin:
                builtin_dir = BUILTIN_MODULES_DIR / mod.builtin_source
                builtin_script = builtin_dir / "module.py"
                if builtin_script.exists():
                    target_dir = MODULES_DIR / mod.name
                    target_dir.mkdir(parents=True, exist_ok=True)
                    target_script = target_dir / "module.py"
                    shutil.copy2(str(builtin_script), str(target_script))
                    builtin_config = builtin_dir / "config.toml"
                    if builtin_config.exists():
                        target_config = target_dir / "config.toml"
                        shutil.copy2(str(builtin_config), str(target_config))
                    script_path = target_script
                    logger.info(f"Restored builtin module '%s' files from '%s'", mod.name, builtin_dir)

            if not script_path.exists() or mod.script_path != expected_script:
                logger.warning(
                    "Module '%s' has stale script_path '%s', updating to '%s'",
                    mod.name, mod.script_path, expected_script,
                )
                mod.script_path = expected_script
                repaired += 1

            if mod.config_path:
                config_path = self._resolve_path(mod.config_path)
                if not config_path.exists() or mod.config_path != expected_config:
                    logger.warning(
                        "Module '%s' has stale config_path '%s', updating to '%s'",
                        mod.name, mod.config_path, expected_config,
                    )
                    mod.config_path = expected_config
                    repaired += 1

        if repaired > 0:
            try:
                await session.commit()
                logger.info(f"Repaired {repaired} module path entries")
            except Exception as e:
                await session.rollback()
                logger.error(f"Failed to commit module path repairs: {e}")
        return repaired

    async def _get_module(self, session: AsyncSession, name: str) -> Module:
        result = await session.execute(select(Module).where(Module.name == name))
        mod = result.scalar_one_or_none()
        if mod is None:
            raise ModuleNotFoundError(f"Module '{name}' not found")
        return mod
