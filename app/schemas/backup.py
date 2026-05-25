"""Backup & Restore Pydantic schemas."""
from datetime import datetime

from pydantic import BaseModel, Field


# ── Backup Items ────────────────────────────────────────

class BackupModuleItem(BaseModel):
    """Module entry in backup items list."""
    name: str
    display_name: str
    is_builtin: bool = False


class BackupServiceItem(BaseModel):
    """Service entry in backup items list."""
    name: str
    display_name: str | None
    status: str


class BackupItemsResponse(BaseModel):
    """Response for GET /backup/items."""
    modules: list[BackupModuleItem]
    services: list[BackupServiceItem]


# ── Create Backup ───────────────────────────────────────

class BackupCreateRequest(BaseModel):
    """Request body for POST /backup/create."""
    modules: list[str] | None = Field(None, description="Module names to backup (null/empty = all non-builtin)")
    services: list[str] | None = Field(None, description="Service names to backup (null/empty = all)")
    include_bindings: bool = True


# ── Manifest ────────────────────────────────────────────

class BindingInfo(BaseModel):
    """Module binding info stored in backup."""
    module_name: str
    enabled: bool = True
    load_order: int = 0


class ManifestModule(BaseModel):
    """Module summary in manifest.json."""
    name: str
    display_name: str
    description: str | None = None
    version: str = "1.0.0"
    author: str | None = None
    code_source: str = "editor"
    requirements: list[str] = Field(default_factory=list)
    is_builtin: bool = False
    has_config: bool = False


class ManifestService(BaseModel):
    """Service summary in manifest.json."""
    name: str
    display_name: str | None = None
    description: str | None = None
    code_source: str = "editor"
    auto_restart: bool = True
    requirements: list[str] = Field(default_factory=list)
    remarks: str | None = None
    bindings: list[BindingInfo] = Field(default_factory=list)


class BackupManifest(BaseModel):
    """Top-level manifest.json structure."""
    version: str = "1.0"
    app_version: str = "0.1.0"
    created_at: str
    source_host: str = "unknown"
    modules: list[ManifestModule] = Field(default_factory=list)
    services: list[ManifestService] = Field(default_factory=list)


# ── Preview ─────────────────────────────────────────────

class ConflictItem(BaseModel):
    """Single conflict item detected during preview."""
    name: str
    display_name: str | None = None
    conflict_type: str  # "exists" | "exists_running" | "builtin"
    local_version: str | None = None
    backup_version: str | None = None
    local_status: str | None = None


class BackupConflicts(BaseModel):
    """Conflicts detected during backup preview."""
    modules: list[ConflictItem] = Field(default_factory=list)
    services: list[ConflictItem] = Field(default_factory=list)
    missing_modules: list[str] = Field(default_factory=list, description="Module names referenced by bindings but not in backup or local")


class BackupPreviewResponse(BaseModel):
    """Response for POST /backup/preview."""
    manifest: BackupManifest
    conflicts: BackupConflicts


# ── Restore ─────────────────────────────────────────────

class RestoreOptions(BaseModel):
    """Options for restore operation."""
    module_conflicts: dict[str, str] = Field(default_factory=dict, description='{"module-name": "skip|overwrite|rename:new-name"}')
    service_conflicts: dict[str, str] = Field(default_factory=dict, description='{"service-name": "skip|stop_and_overwrite|rename:new-name"}')


class RestoredBinding(BaseModel):
    """A restored binding record."""
    service: str
    module: str
    enabled: bool
    load_order: int


class RestoreResponse(BaseModel):
    """Response for POST /backup/restore."""
    restored_modules: list[str] = Field(default_factory=list)
    restored_services: list[str] = Field(default_factory=list)
    skipped_modules: list[str] = Field(default_factory=list)
    skipped_services: list[str] = Field(default_factory=list)
    stopped_services: list[str] = Field(default_factory=list)
    restored_bindings: list[RestoredBinding] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
