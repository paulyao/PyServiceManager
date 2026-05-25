"""Backup & Restore management: create, preview, restore ZIP archives."""
import io
import json
import logging
import socket
import zipfile
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DATA_DIR, SERVICES_DIR, MODULES_DIR, DEFAULT_PYTHON_PATH
from app.models.module import Module
from app.models.service import Service
from app.models.service_module import ServiceModule
from app.schemas.backup import (
    BackupManifest, ManifestModule, ManifestService, BindingInfo,
    BackupConflicts, ConflictItem, BackupPreviewResponse,
    RestoreOptions, RestoreResponse, RestoredBinding,
)
from app.utils.validation import validate_service_name

logger = logging.getLogger(__name__)

# Files/dirs to exclude from service backup (auto-generated)
_SERVICE_EXCLUDE = {"runner.py", "runner.log", ".pid", ".modules.json", "__pycache__"}
_MODULE_EXCLUDE = {"__pycache__"}

# Supported backup manifest version
_BACKUP_VERSION = "1.0"


class BackupManagerError(Exception):
    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail


class BackupManager:
    """Manages backup creation, preview, and restore operations."""

    @staticmethod
    def _relative_path(abs_path: Path | str) -> str:
        """Convert absolute path to relative from project root."""
        p = Path(abs_path)
        try:
            return str(p.relative_to(DATA_DIR.parent))
        except ValueError:
            return str(p)

    @staticmethod
    def _resolve_path(stored_path: str) -> Path:
        """Resolve stored path to absolute Path."""
        p = Path(stored_path)
        if p.is_absolute():
            return p
        return DATA_DIR.parent / stored_path

    @staticmethod
    def _validate_zip_path(name: str) -> bool:
        """Check ZIP entry name for path traversal attacks."""
        if name.startswith("/") or ".." in name.split("/"):
            return False
        return True

    async def get_backup_items(self, session: AsyncSession) -> dict:
        """Get list of modules and services available for backup."""
        # Non-builtin modules only
        mod_result = await session.execute(
            select(Module).where(Module.is_builtin == False).order_by(Module.name)  # noqa: E712
        )
        modules = [
            {"name": m.name, "display_name": m.display_name, "is_builtin": False}
            for m in mod_result.scalars().all()
        ]

        svc_result = await session.execute(select(Service).order_by(Service.name))
        services = [
            {"name": s.name, "display_name": s.display_name, "status": s.status}
            for s in svc_result.scalars().all()
        ]

        return {"modules": modules, "services": services}

    async def create_backup(
        self,
        session: AsyncSession,
        module_names: list[str] | None,
        service_names: list[str] | None,
        include_bindings: bool = True,
    ) -> tuple[io.BytesIO, str]:
        """Create a ZIP backup archive. Returns (zip_buffer, filename)."""
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"pyservice-backup-{timestamp}.zip"

        buffer = io.BytesIO()
        manifest_modules: list[ManifestModule] = []
        manifest_services: list[ManifestService] = []

        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            # ── Backup Modules ──
            mod_query = select(Module).where(Module.is_builtin == False).order_by(Module.name)  # noqa: E712
            if module_names is not None and len(module_names) > 0:
                mod_query = mod_query.where(Module.name.in_(module_names))

            mod_result = await session.execute(mod_query)
            modules = mod_result.scalars().all()

            for mod in modules:
                mod_prefix = f"modules/{mod.name}"
                script_path = self._resolve_path(mod.script_path)

                # Write module.py
                if script_path.exists():
                    zf.writestr(f"{mod_prefix}/module.py", script_path.read_text(encoding="utf-8"))
                else:
                    logger.warning("Module '%s' script_path not found: %s", mod.name, script_path)

                # Write config.toml if exists
                has_config = False
                if mod.config_path:
                    config_path = self._resolve_path(mod.config_path)
                    if config_path.exists():
                        zf.writestr(f"{mod_prefix}/config.toml", config_path.read_text(encoding="utf-8"))
                        has_config = True

                # Write meta.json
                meta = {
                    "name": mod.name,
                    "display_name": mod.display_name,
                    "description": mod.description,
                    "version": mod.version,
                    "author": mod.author,
                    "code_source": mod.code_source,
                    "requirements": mod.requirements_list,
                }
                zf.writestr(f"{mod_prefix}/meta.json", json.dumps(meta, ensure_ascii=False, indent=2))

                manifest_modules.append(ManifestModule(
                    name=mod.name,
                    display_name=mod.display_name,
                    description=mod.description,
                    version=mod.version,
                    author=mod.author,
                    code_source=mod.code_source,
                    requirements=mod.requirements_list,
                    is_builtin=False,
                    has_config=has_config,
                ))

            # ── Backup Services ──
            svc_query = select(Service).order_by(Service.name)
            if service_names is not None and len(service_names) > 0:
                svc_query = svc_query.where(Service.name.in_(service_names))

            svc_result = await session.execute(svc_query)
            services = svc_result.scalars().all()

            for svc in services:
                svc_prefix = f"services/{svc.name}"
                service_dir = SERVICES_DIR / svc.name

                # Write main.py
                main_path = service_dir / "main.py"
                if main_path.exists():
                    zf.writestr(f"{svc_prefix}/main.py", main_path.read_text(encoding="utf-8"))

                # Write config.toml
                config_path = service_dir / "config.toml"
                if config_path.exists():
                    zf.writestr(f"{svc_prefix}/config.toml", config_path.read_text(encoding="utf-8"))

                # Collect bindings
                bindings: list[BindingInfo] = []
                if include_bindings:
                    bindings_result = await session.execute(
                        select(ServiceModule)
                        .where(ServiceModule.service_id == svc.id)
                        .order_by(ServiceModule.load_order)
                    )
                    for binding in bindings_result.scalars().all():
                        mod_result2 = await session.execute(select(Module).where(Module.id == binding.module_id))
                        bound_mod = mod_result2.scalar_one_or_none()
                        if bound_mod:
                            bindings.append(BindingInfo(
                                module_name=bound_mod.name,
                                enabled=binding.enabled,
                                load_order=binding.load_order,
                            ))

                # Write meta.json
                meta = {
                    "name": svc.name,
                    "display_name": svc.display_name,
                    "description": svc.description,
                    "code_source": svc.code_source,
                    "auto_restart": svc.auto_restart,
                    "requirements": svc.requirements_list,
                    "remarks": svc.remarks,
                    "bindings": [b.model_dump() for b in bindings],
                }
                zf.writestr(f"{svc_prefix}/meta.json", json.dumps(meta, ensure_ascii=False, indent=2))

                manifest_services.append(ManifestService(
                    name=svc.name,
                    display_name=svc.display_name,
                    description=svc.description,
                    code_source=svc.code_source,
                    auto_restart=svc.auto_restart,
                    requirements=svc.requirements_list,
                    remarks=svc.remarks,
                    bindings=bindings,
                ))

            # ── Write manifest.json ──
            manifest = BackupManifest(
                version=_BACKUP_VERSION,
                created_at=datetime.now().isoformat(timespec="seconds"),
                source_host=socket.gethostname(),
                modules=manifest_modules,
                services=manifest_services,
            )
            zf.writestr("manifest.json", json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2))

        buffer.seek(0)
        return buffer, filename

    async def preview_backup(self, session: AsyncSession, zip_bytes: bytes) -> BackupPreviewResponse:
        """Preview a backup ZIP: parse manifest and detect conflicts."""
        manifest = self._read_manifest(zip_bytes)

        conflicts = BackupConflicts()
        backup_module_names = {m.name for m in manifest.modules}
        backup_service_names = {s.name for s in manifest.services}

        # Check module conflicts
        for mod in manifest.modules:
            if mod.is_builtin:
                conflicts.modules.append(ConflictItem(
                    name=mod.name,
                    display_name=mod.display_name,
                    conflict_type="builtin",
                    backup_version=mod.version,
                ))
                continue

            existing = await session.execute(select(Module).where(Module.name == mod.name))
            local_mod = existing.scalar_one_or_none()
            if local_mod:
                conflicts.modules.append(ConflictItem(
                    name=mod.name,
                    display_name=mod.display_name,
                    conflict_type="exists",
                    local_version=local_mod.version,
                    backup_version=mod.version,
                ))

        # Check service conflicts
        for svc in manifest.services:
            existing = await session.execute(select(Service).where(Service.name == svc.name))
            local_svc = existing.scalar_one_or_none()
            if local_svc:
                conflict_type = "exists_running" if local_svc.status == "running" else "exists"
                conflicts.services.append(ConflictItem(
                    name=svc.name,
                    display_name=svc.display_name,
                    conflict_type=conflict_type,
                    local_status=local_svc.status,
                ))

        # Check for missing modules referenced by bindings
        all_local_module_names = set()
        local_mods = await session.execute(select(Module.name))
        all_local_module_names = {m for (m,) in local_mods.all()}

        for svc in manifest.services:
            for binding in svc.bindings:
                if binding.module_name not in backup_module_names and binding.module_name not in all_local_module_names:
                    if binding.module_name not in conflicts.missing_modules:
                        conflicts.missing_modules.append(binding.module_name)

        return BackupPreviewResponse(manifest=manifest, conflicts=conflicts)

    async def restore_backup(
        self,
        session: AsyncSession,
        zip_bytes: bytes,
        options: RestoreOptions,
        service_manager=None,
    ) -> RestoreResponse:
        """Execute backup restore with conflict resolution."""
        manifest = self._read_manifest(zip_bytes)
        result = RestoreResponse()

        # Read all ZIP entries into memory
        entries = self._read_zip_entries(zip_bytes)

        # ── Phase 1: Restore Modules ──
        for mod_info in manifest.modules:
            if mod_info.is_builtin:
                result.warnings.append(f"跳过内置模块 '{mod_info.name}'（目标机器会自动注册）")
                continue

            strategy = options.module_conflicts.get(mod_info.name, "skip")

            # Check if module exists locally
            existing = await session.execute(select(Module).where(Module.name == mod_info.name))
            local_mod = existing.scalar_one_or_none()

            if local_mod and strategy == "skip":
                result.skipped_modules.append(mod_info.name)
                continue

            if strategy.startswith("rename:"):
                new_name = strategy.split(":", 1)[1].strip()
                try:
                    validate_service_name(new_name)
                except ValueError as e:
                    result.errors.append(f"模块 '{mod_info.name}' 重命名失败: {str(e)}")
                    continue
                # Check new name doesn't conflict
                dup = await session.execute(select(Module).where(Module.name == new_name))
                if dup.scalar_one_or_none():
                    result.errors.append(f"模块 '{mod_info.name}' 重命名为 '{new_name}' 失败: 名称已存在")
                    continue
            elif strategy == "overwrite" and not local_mod:
                # Not a conflict, just new — proceed as new
                pass
            elif local_mod is None:
                # New module, proceed
                pass

            try:
                effective_name = mod_info.name
                if strategy.startswith("rename:"):
                    effective_name = strategy.split(":", 1)[1].strip()

                # Read files from ZIP
                mod_prefix = f"modules/{mod_info.name}"
                code = entries.get(f"{mod_prefix}/module.py", "")
                config_toml = entries.get(f"{mod_prefix}/config.toml")
                meta_raw = entries.get(f"{mod_prefix}/meta.json", "{}")
                meta = json.loads(meta_raw)

                if not code:
                    result.errors.append(f"模块 '{mod_info.name}' 备份中缺少 module.py")
                    continue

                module_dir = MODULES_DIR / effective_name
                module_dir.mkdir(parents=True, exist_ok=True)

                # Write module.py
                (module_dir / "module.py").write_text(code, encoding="utf-8")

                # Write config.toml
                if config_toml is not None:
                    (module_dir / "config.toml").write_text(config_toml, encoding="utf-8")

                if local_mod and strategy == "overwrite":
                    # Update existing module metadata
                    local_mod.display_name = meta.get("display_name", mod_info.display_name)
                    local_mod.description = meta.get("description", mod_info.description)
                    local_mod.version = meta.get("version", mod_info.version)
                    local_mod.author = meta.get("author", mod_info.author)
                    local_mod.code_source = meta.get("code_source", mod_info.code_source)
                    local_mod.requirements = json.dumps(meta.get("requirements", []))
                    local_mod.script_path = self._relative_path(module_dir / "module.py")
                    local_mod.config_path = self._relative_path(module_dir / "config.toml") if config_toml else None
                    local_mod.updated_at = datetime.now()
                    result.restored_modules.append(effective_name)
                    result.warnings.append(f"模块 '{effective_name}' 已覆盖")
                else:
                    # Create new module record
                    new_mod = Module(
                        name=effective_name,
                        display_name=meta.get("display_name", effective_name),
                        description=meta.get("description"),
                        version=meta.get("version", "1.0.0"),
                        author=meta.get("author"),
                        code_source=meta.get("code_source", "editor"),
                        script_path=self._relative_path(module_dir / "module.py"),
                        config_path=self._relative_path(module_dir / "config.toml") if config_toml else None,
                        requirements=json.dumps(meta.get("requirements", [])),
                    )
                    session.add(new_mod)
                    result.restored_modules.append(effective_name)

            except Exception as e:
                result.errors.append(f"恢复模块 '{mod_info.name}' 失败: {str(e)}")
                logger.exception("Failed to restore module '%s'", mod_info.name)

        # Commit module changes before proceeding to services
        try:
            await session.commit()
        except Exception as e:
            await session.rollback()
            result.errors.append(f"模块数据提交失败: {str(e)}")

        # ── Phase 2: Restore Services ──
        for svc_info in manifest.services:
            strategy = options.service_conflicts.get(svc_info.name, "skip")

            # Check if service exists locally
            existing = await session.execute(select(Service).where(Service.name == svc_info.name))
            local_svc = existing.scalar_one_or_none()

            if local_svc and strategy == "skip":
                result.skipped_services.append(svc_info.name)
                continue

            if strategy.startswith("rename:"):
                new_name = strategy.split(":", 1)[1].strip()
                try:
                    validate_service_name(new_name)
                except ValueError as e:
                    result.errors.append(f"服务 '{svc_info.name}' 重命名失败: {str(e)}")
                    continue
                dup = await session.execute(select(Service).where(Service.name == new_name))
                if dup.scalar_one_or_none():
                    result.errors.append(f"服务 '{svc_info.name}' 重命名为 '{new_name}' 失败: 名称已存在")
                    continue

            try:
                effective_name = svc_info.name
                if strategy.startswith("rename:"):
                    effective_name = strategy.split(":", 1)[1].strip()

                # Stop running service if needed
                if local_svc and local_svc.status == "running" and strategy == "stop_and_overwrite":
                    if service_manager:
                        try:
                            await service_manager.stop(session, effective_name)
                            result.stopped_services.append(effective_name)
                        except Exception as e:
                            result.errors.append(f"停止服务 '{effective_name}' 失败: {str(e)}")
                            continue

                # Read files from ZIP
                svc_prefix = f"services/{svc_info.name}"
                code = entries.get(f"{svc_prefix}/main.py", "")
                config = entries.get(f"{svc_prefix}/config.toml")
                meta_raw = entries.get(f"{svc_prefix}/meta.json", "{}")
                meta = json.loads(meta_raw)

                if not code:
                    result.errors.append(f"服务 '{svc_info.name}' 备份中缺少 main.py")
                    continue

                service_dir = SERVICES_DIR / effective_name
                service_dir.mkdir(parents=True, exist_ok=True)

                # Write main.py
                (service_dir / "main.py").write_text(code, encoding="utf-8")

                # Write config.toml
                if config is not None:
                    (service_dir / "config.toml").write_text(config, encoding="utf-8")

                # Resolve python_path for this machine
                resolved_python = DEFAULT_PYTHON_PATH

                # Generate runner.py and unit file
                from app.core.runner_template import generate_runner, generate_unit
                generate_runner(effective_name, service_dir, resolved_python)
                generate_unit(
                    effective_name,
                    meta.get("display_name", effective_name),
                    service_dir,
                    resolved_python,
                    meta.get("auto_restart", True),
                )

                if local_svc and strategy in ("overwrite", "stop_and_overwrite"):
                    # Update existing service metadata
                    local_svc.display_name = meta.get("display_name", svc_info.display_name)
                    local_svc.description = meta.get("description", svc_info.description)
                    local_svc.code_source = meta.get("code_source", svc_info.code_source)
                    local_svc.auto_restart = meta.get("auto_restart", svc_info.auto_restart)
                    local_svc.requirements = json.dumps(meta.get("requirements", []))
                    local_svc.remarks = meta.get("remarks", svc_info.remarks)
                    local_svc.python_path = resolved_python
                    local_svc.working_dir = str(service_dir)
                    local_svc.script_path = self._relative_path(service_dir / "main.py")
                    local_svc.config_path = self._relative_path(service_dir / "config.toml")
                    local_svc.status = "stopped"
                    local_svc.updated_at = datetime.now()
                    result.restored_services.append(effective_name)
                    if strategy == "stop_and_overwrite":
                        result.warnings.append(f"服务 '{effective_name}' 已停止并覆盖")
                    else:
                        result.warnings.append(f"服务 '{effective_name}' 已覆盖")
                else:
                    # Create new service record
                    new_svc = Service(
                        name=effective_name,
                        display_name=meta.get("display_name", effective_name),
                        description=meta.get("description"),
                        code_source=meta.get("code_source", "editor"),
                        script_path=self._relative_path(service_dir / "main.py"),
                        config_path=self._relative_path(service_dir / "config.toml"),
                        status="stopped",
                        enabled=False,
                        auto_restart=meta.get("auto_restart", True),
                        python_path=resolved_python,
                        working_dir=str(service_dir),
                        requirements=json.dumps(meta.get("requirements", [])),
                        remarks=meta.get("remarks"),
                    )
                    session.add(new_svc)
                    result.restored_services.append(effective_name)

            except Exception as e:
                result.errors.append(f"恢复服务 '{svc_info.name}' 失败: {str(e)}")
                logger.exception("Failed to restore service '%s'", svc_info.name)

        # Commit service changes before bindings
        try:
            await session.commit()
        except Exception as e:
            await session.rollback()
            result.errors.append(f"服务数据提交失败: {str(e)}")

        # ── Phase 3: Restore Bindings ──
        for svc_info in manifest.services:
            if not svc_info.bindings:
                continue

            effective_svc_name = svc_info.name
            # If service was renamed, use the new name
            svc_strategy = options.service_conflicts.get(svc_info.name, "skip")
            if svc_strategy.startswith("rename:"):
                effective_svc_name = svc_strategy.split(":", 1)[1].strip()
            elif svc_strategy == "skip":
                continue

            # Check if service was actually restored
            if effective_svc_name not in result.restored_services:
                continue

            # Find the local service
            svc_result = await session.execute(select(Service).where(Service.name == effective_svc_name))
            local_svc = svc_result.scalar_one_or_none()
            if not local_svc:
                continue

            for binding in svc_info.bindings:
                try:
                    # Resolve module by name — may be local builtin, restored, or existing
                    mod_result = await session.execute(select(Module).where(Module.name == binding.module_name))
                    local_mod = mod_result.scalar_one_or_none()

                    if not local_mod:
                        result.warnings.append(
                            f"服务 '{effective_svc_name}' 的绑定模块 '{binding.module_name}' 在本地不存在，跳过绑定"
                        )
                        continue

                    # Check if binding already exists
                    existing_binding = await session.execute(
                        select(ServiceModule).where(
                            ServiceModule.service_id == local_svc.id,
                            ServiceModule.module_id == local_mod.id,
                        )
                    )
                    if existing_binding.scalar_one_or_none():
                        # Update existing binding
                        existing_bind = existing_binding.scalar_one()
                        existing_bind.enabled = binding.enabled
                        existing_bind.load_order = binding.load_order
                    else:
                        # Create new binding
                        new_binding = ServiceModule(
                            service_id=local_svc.id,
                            module_id=local_mod.id,
                            enabled=binding.enabled,
                            load_order=binding.load_order,
                        )
                        session.add(new_binding)

                    result.restored_bindings.append(RestoredBinding(
                        service=effective_svc_name,
                        module=binding.module_name,
                        enabled=binding.enabled,
                        load_order=binding.load_order,
                    ))

                except Exception as e:
                    result.errors.append(
                        f"恢复绑定 '{effective_svc_name}' → '{binding.module_name}' 失败: {str(e)}"
                    )

            # Refresh .modules.json for this service
            try:
                await self._write_modules_registry(session, local_svc)
            except Exception as e:
                result.warnings.append(f"刷新服务 '{effective_svc_name}' 的 .modules.json 失败: {str(e)}")

        # Final commit for bindings
        try:
            await session.commit()
        except Exception as e:
            await session.rollback()
            result.errors.append(f"绑定数据提交失败: {str(e)}")

        return result

    def _read_manifest(self, zip_bytes: bytes) -> BackupManifest:
        """Read and validate manifest.json from ZIP bytes."""
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
                # Security: validate all entry names
                for name in zf.namelist():
                    if not self._validate_zip_path(name):
                        raise BackupManagerError(f"备份文件包含非法路径: {name}")

                if "manifest.json" not in zf.namelist():
                    raise BackupManagerError("无效的备份文件: 缺少 manifest.json")

                manifest_data = json.loads(zf.read("manifest.json"))
                manifest = BackupManifest.model_validate(manifest_data)

                if manifest.version != _BACKUP_VERSION:
                    raise BackupManagerError(
                        f"备份版本 '{manifest.version}' 不受支持，当前支持版本: {_BACKUP_VERSION}"
                    )

                return manifest
        except zipfile.BadZipFile:
            raise BackupManagerError("无效的 ZIP 文件格式")
        except BackupManagerError:
            raise
        except Exception as e:
            raise BackupManagerError(f"读取备份文件失败: {str(e)}")

    def _read_zip_entries(self, zip_bytes: bytes) -> dict[str, str]:
        """Read all text entries from ZIP into a dict. Skips directories."""
        entries = {}
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if not self._validate_zip_path(info.filename):
                    continue
                try:
                    entries[info.filename] = zf.read(info.filename).decode("utf-8")
                except UnicodeDecodeError:
                    logger.warning("Skipping non-text entry in backup: %s", info.filename)
        return entries

    async def _write_modules_registry(self, session: AsyncSession, service: Service) -> None:
        """Write .modules.json for a service (mirrors ModuleManager logic)."""
        from app.core.module_manager import ModuleManager

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
                    "script_path": str(ModuleManager._resolve_path(mod.script_path)),
                    "config_path": str(ModuleManager._resolve_path(mod.config_path)) if mod.config_path else "",
                })

        registry_path = SERVICES_DIR / service.name / ".modules.json"
        registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
