"""Module Pydantic schemas."""
import json
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class ModuleCreate(BaseModel):
    name: str = Field(..., pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$", description="Module name")
    display_name: str
    description: str | None = None
    version: str = "1.0.0"
    author: str | None = None
    code: str | None = None
    config_toml: str | None = None
    code_source: str = Field("editor", pattern=r"^(editor|upload)$")
    requirements: list[str] = Field(default_factory=list, description="Pip package requirements")


class ModuleUpdate(BaseModel):
    name: str | None = Field(None, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$", description="New module name (renames directory and updates paths)")
    display_name: str | None = None
    description: str | None = None
    version: str | None = None
    author: str | None = None
    requirements: list[str] | None = None


class ModuleCodeUpdate(BaseModel):
    code: str
    config_toml: str | None = None


class ModuleResponse(BaseModel):
    id: int
    name: str
    display_name: str
    description: str | None
    version: str
    author: str | None
    code_source: str
    is_builtin: bool = False
    requirements: list[str] = Field(default_factory=list)
    service_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("requirements", mode="before")
    @classmethod
    def parse_requirements(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v


class ModuleCodeResponse(BaseModel):
    code: str
    config_toml: str | None = None


class ModuleValidateRequest(BaseModel):
    code: str


class ModuleValidateResponse(BaseModel):
    valid: bool
    errors: list[str] = []
    module_info: dict | None = None


class ServiceModuleItem(BaseModel):
    module_id: int
    name: str
    display_name: str
    version: str
    description: str | None
    enabled: bool
    load_order: int


class ServiceModulesUpdate(BaseModel):
    modules: list[dict] = Field(..., description="List of {module_id, enabled, load_order}")


class ServiceModulesResponse(BaseModel):
    modules: list[ServiceModuleItem]
    restart_required: bool = False


# ── Dependency schemas ────────────────────────────────

class PkgStatusItem(BaseModel):
    name: str
    specifier: str
    installed: bool
    installed_version: str | None
    satisfied: bool


class DepsCheckResponse(BaseModel):
    requirements: list[PkgStatusItem]
    all_satisfied: bool
    missing_count: int


class DepsInstallResponse(BaseModel):
    success: bool
    installed: list[str]
    failed: list[str]
    output: str


class ScannedImportItem(BaseModel):
    """Single import statement found by source code scanning."""
    module_name: str
    full_path: str
    import_type: str        # "stdlib" / "third_party" / "local"
    line_number: int
    pip_name: str | None


class SourceScanResultItem(BaseModel):
    """Scan result for a single source file."""
    file_path: str
    imports: list[ScannedImportItem]
    third_party_packages: list[str]
    error: str | None = None


class RequirementsUpdate(BaseModel):
    requirements: list[str]
